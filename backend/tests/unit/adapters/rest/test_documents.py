"""Upload, status, processing log, retry and reprocessing over HTTP."""

import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID

import httpx
import pytest

from papiq.adapters.inbound.rest import PREFIX
from papiq.composition.container import build_memory_container, build_services
from papiq.core.domain.documents import Sha256
from papiq.core.domain.drawers import ShareLevel
from papiq.core.domain.errors import UnprocessableDocumentError
from papiq.core.domain.pipeline import Step
from papiq.core.services.objects import original_key
from tests.contracts.processing import SAMPLES
from tests.unit.adapters.rest.conftest import MAX_UPLOAD, USER_HEADER, Api, auth, make_app

DOCUMENTS = f"{PREFIX}/documents"
PROBLEM = "application/problem+json"


def pdf(name: str = "scan.pdf") -> dict[str, tuple[str, bytes, str]]:
    return {"file": (name, (SAMPLES / name).read_bytes(), "application/octet-stream")}


async def test_upload_is_accepted_and_processed(api: Api) -> None:
    owner = await api.user()
    response = await api.client.post(DOCUMENTS, files=pdf(), headers=auth(owner))
    assert response.status_code == 202
    id = response.json()["id"]
    assert response.headers["location"] == response.json()["status_url"] == f"{DOCUMENTS}/{id}"

    status = (await api.client.get(f"{DOCUMENTS}/{id}", headers=auth(owner))).json()
    assert status["media_type"] == "application/pdf"
    assert status["original_filename"] == "scan.pdf"
    assert status["lane"] is None
    assert status["processing"] == {
        "status": "processing",
        "current_step": "ocr",
        "run": 1,
        "outcomes": {"receive": "ok"},
    }

    await api.drain()
    status = (await api.client.get(f"{DOCUMENTS}/{id}", headers=auth(owner))).json()
    assert (status["lane"], status["processing"]["status"]) == ("green", "completed")
    log = (await api.client.get(f"{DOCUMENTS}/{id}/log", headers=auth(owner))).json()
    assert [entry["step"] for entry in log][:3] == ["receive", "ocr", "parse"]
    assert log[0]["output"]["sha256"]
    assert log[1]["model_version"] == "fake-ocr 1"


async def test_a_duplicate_of_the_same_owner_is_rejected(api: Api) -> None:
    owner, other = await api.user(), await api.user()
    first = await api.client.post(DOCUMENTS, files=pdf(), headers=auth(owner))
    again = await api.client.post(DOCUMENTS, files=pdf(), headers=auth(owner))
    assert again.status_code == 409
    assert again.headers["content-type"] == PROBLEM
    assert again.json()["existing_document_id"] == first.json()["id"]
    assert again.json()["title"] == "Conflict"
    # Another owner gets a document of their own for the same file.
    assert (await api.client.post(DOCUMENTS, files=pdf(), headers=auth(other))).status_code == 202


async def test_unsupported_files_are_rejected(api: Api) -> None:
    owner = await api.user()
    file = {"file": ("letter.pdf", (SAMPLES / "unsupported.docx").read_bytes(), "x/y")}
    response = await api.client.post(DOCUMENTS, files=file, headers=auth(owner))
    assert response.status_code == 415
    assert "unsupported file type" in response.json()["detail"]


async def test_a_photo_is_accepted(api: Api) -> None:
    owner = await api.user()
    response = await api.client.post(DOCUMENTS, files=pdf("photo.jpg"), headers=auth(owner))
    assert response.status_code == 202
    status = await api.client.get(response.json()["status_url"], headers=auth(owner))
    assert status.json()["media_type"] == "image/jpeg"


async def test_a_too_large_file_is_rejected(api: Api) -> None:
    owner = await api.user()
    files = {"file": ("big.pdf", b"%PDF-1.7\n" + b"x" * MAX_UPLOAD, "application/pdf")}
    response = await api.client.post(DOCUMENTS, files=files, headers=auth(owner))
    assert response.status_code == 413
    assert response.json()["detail"] == f"the file is larger than {MAX_UPLOAD} bytes"


async def test_the_limit_holds_without_content_length(api: Api) -> None:
    """A chunked body is stopped once the file part passes the limit."""
    owner = await api.user()
    boundary = "papiq-test-boundary"
    sent = 0

    async def body() -> AsyncIterator[bytes]:
        nonlocal sent
        yield (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
            'filename="big.pdf"\r\n\r\n%PDF-1.7\n'
        ).encode()
        for _ in range(4 * MAX_UPLOAD // 65536):
            sent += 65536
            yield b"x" * 65536
        yield f"\r\n--{boundary}--\r\n".encode()

    response = await api.client.post(
        DOCUMENTS,
        content=body(),
        headers={**auth(owner), "content-type": f"multipart/form-data; boundary={boundary}"},
    )
    assert response.status_code == 413
    assert sent < 2 * MAX_UPLOAD  # reading stopped early


@pytest.mark.parametrize(
    ("kwargs", "status", "detail"),
    [
        ({"files": {"other": ("a.pdf", b"%PDF-1.7")}}, 400, "no file part"),
        (
            {"content": b"%PDF-1.7", "headers": {"content-type": "application/pdf"}},
            400,
            "multipart",
        ),
        ({"files": pdf(), "data": {"drawer_id": "no-uuid"}}, 422, "drawer_id"),
    ],
)
async def test_malformed_uploads(
    api: Api, kwargs: dict[str, object], status: int, detail: str
) -> None:
    owner = await api.user()
    headers = {**auth(owner), **kwargs.pop("headers", {})}  # type: ignore[dict-item]
    response = await api.client.post(DOCUMENTS, headers=headers, **kwargs)  # type: ignore[arg-type]
    assert response.status_code == status, response.text
    assert detail in response.json()["detail"]


async def test_upload_into_a_drawer(api: Api) -> None:
    owner, writer, reader = await api.user(), await api.user(), await api.user()
    drawers = api.services  # drawer service lives in the services, not in the API (M4)
    shared = await drawers.drawers.create(owner.id, "Household")
    await drawers.drawers.share(owner.id, shared.id, writer.id, ShareLevel.READ_WRITE)
    await drawers.drawers.share(owner.id, shared.id, reader.id, ShareLevel.READ)
    data = {"drawer_id": str(shared.id)}

    accepted = await api.client.post(DOCUMENTS, files=pdf(), data=data, headers=auth(writer))
    assert accepted.status_code == 202
    status = await api.client.get(accepted.json()["status_url"], headers=auth(writer))
    assert status.json()["drawer_id"] == str(shared.id)
    denied = await api.client.post(DOCUMENTS, files=pdf(), data=data, headers=auth(reader))
    assert denied.status_code == 403
    stranger = await api.user()
    hidden = await api.client.post(DOCUMENTS, files=pdf(), data=data, headers=auth(stranger))
    assert hidden.status_code == 404


async def test_the_filename_is_reduced_to_its_last_segment(api: Api) -> None:
    owner = await api.user()
    files = {"file": ("../../etc/rechnung.pdf", (SAMPLES / "scan.pdf").read_bytes(), "x/y")}
    response = await api.client.post(DOCUMENTS, files=files, headers=auth(owner))
    status = await api.client.get(response.json()["status_url"], headers=auth(owner))
    assert status.json()["original_filename"] == "rechnung.pdf"
    assert status.json()["title"] == "rechnung"


async def test_received_files_are_removed(
    api: Api, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    owner = await api.user()
    await api.client.post(DOCUMENTS, files=pdf(), headers=auth(owner))
    await api.client.post(DOCUMENTS, files=pdf(), headers=auth(owner))  # duplicate
    await api.client.post(DOCUMENTS, files=pdf("unsupported.docx"), headers=auth(owner))
    big = {"file": ("big.pdf", b"x" * (MAX_UPLOAD + 1), "x/y")}
    await api.client.post(DOCUMENTS, files=big, headers=auth(owner))
    assert list(tmp_path.iterdir()) == []


async def test_the_original_is_stored_by_its_hash(api: Api) -> None:
    owner = await api.user()
    response = await api.client.post(DOCUMENTS, files=pdf(), headers=auth(owner))
    log = await api.client.get(f"{response.json()['status_url']}/log", headers=auth(owner))
    key = original_key(Sha256(log.json()[0]["output"]["sha256"]))
    assert await api.container.object_store.get(key) == (SAMPLES / "scan.pdf").read_bytes()


async def test_others_see_a_document_only_through_a_share_once_green(api: Api) -> None:
    owner, reader, stranger = await api.user(), await api.user(), await api.user()
    shared = await api.services.drawers.create(owner.id, "Shared")
    await api.services.drawers.share(owner.id, shared.id, reader.id, ShareLevel.READ)
    response = await api.client.post(
        DOCUMENTS, files=pdf(), data={"drawer_id": str(shared.id)}, headers=auth(owner)
    )
    url = response.json()["status_url"]
    assert (await api.client.get(url, headers=auth(reader))).status_code == 404  # processing
    await api.drain()
    assert (await api.client.get(url, headers=auth(reader))).status_code == 200
    for path in (url, f"{url}/log"):
        missing = await api.client.get(path, headers=auth(stranger))
        assert missing.status_code == 404
        assert missing.headers["content-type"] == PROBLEM


async def test_retry_and_reprocess(api: Api) -> None:
    class Broken:
        async def run(self, document: object) -> object:
            raise UnprocessableDocumentError("the PDF is encrypted")

    owner = await api.user()
    executors = api.services.pipeline._executors
    working = executors[Step.OCR]
    executors[Step.OCR] = Broken()  # type: ignore[assignment]
    response = await api.client.post(DOCUMENTS, files=pdf(), headers=auth(owner))
    url = response.json()["status_url"]
    await api.drain()
    failed = (await api.client.get(url, headers=auth(owner))).json()
    assert (failed["lane"], failed["processing"]["status"]) == ("red", "failed")

    # Reprocessing must not skip the failed step.
    skip = await api.client.post(
        f"{url}/reprocess", json={"from_step": "parse"}, headers=auth(owner)
    )
    assert skip.status_code == 409

    executors[Step.OCR] = working
    retried = await api.client.post(f"{url}/retry", headers=auth(owner))
    assert retried.status_code == 202
    assert retried.json()["processing"] == {
        "status": "processing",
        "current_step": "ocr",
        "run": 2,
        "outcomes": {"receive": "ok"},
    }
    again = await api.client.post(f"{url}/retry", headers=auth(owner))
    assert again.status_code == 409  # running, not failed
    await api.drain()
    assert (await api.client.get(url, headers=auth(owner))).json()["lane"] == "green"

    reprocessed = await api.client.post(
        f"{url}/reprocess", json={"from_step": "parse"}, headers=auth(owner)
    )
    assert reprocessed.status_code == 202
    assert reprocessed.json()["processing"]["current_step"] == "parse"
    await api.drain()
    log = (await api.client.get(f"{url}/log", headers=auth(owner))).json()
    runs = [(entry["step"], entry["run"]) for entry in log if entry["step"] in ("ocr", "parse")]
    assert runs == [("ocr", 1), ("ocr", 2), ("parse", 2), ("parse", 3)]


async def test_reprocess_validates_the_step(api: Api) -> None:
    owner = await api.user()
    response = await api.client.post(DOCUMENTS, files=pdf(), headers=auth(owner))
    url = response.json()["status_url"]
    await api.drain()
    for body in ({"from_step": "receive"}, {"from_step": "nope"}, {}):
        invalid = await api.client.post(f"{url}/reprocess", json=body, headers=auth(owner))
        assert invalid.status_code == 422, body
        assert invalid.headers["content-type"] == PROBLEM
        assert "from_step" in invalid.json()["detail"]


async def test_only_the_owner_controls_processing(api: Api) -> None:
    owner, writer = await api.user(), await api.user()
    shared = await api.services.drawers.create(owner.id, "Shared")
    await api.services.drawers.share(owner.id, shared.id, writer.id, ShareLevel.READ_WRITE)
    response = await api.client.post(
        DOCUMENTS, files=pdf(), data={"drawer_id": str(shared.id)}, headers=auth(owner)
    )
    url = response.json()["status_url"]
    await api.drain()
    denied = await api.client.post(
        f"{url}/reprocess", json={"from_step": "ocr"}, headers=auth(writer)
    )
    assert denied.status_code == 403
    missing = await api.client.post(f"{DOCUMENTS}/{UUID(int=0)}/retry", headers=auth(owner))
    assert missing.status_code == 404


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", DOCUMENTS),
        ("GET", f"{DOCUMENTS}/{UUID(int=1)}"),
        ("GET", f"{DOCUMENTS}/{UUID(int=1)}/log"),
        ("POST", f"{DOCUMENTS}/{UUID(int=1)}/retry"),
        ("POST", f"{DOCUMENTS}/{UUID(int=1)}/reprocess"),
        ("GET", f"{PREFIX}/events"),
    ],
)
async def test_without_authentication_everything_is_refused(
    api: Api, method: str, path: str
) -> None:
    del api.app.dependency_overrides[next(iter(api.app.dependency_overrides))]
    response = await api.client.request(method, path)
    assert response.status_code == 401
    assert response.headers["content-type"] == PROBLEM
    assert response.headers["www-authenticate"] == "Bearer"
    assert "M4" in response.json()["detail"]


async def test_unauthenticated_uploads_are_not_read(api: Api) -> None:
    """The user is checked before the body is received."""
    del api.app.dependency_overrides[next(iter(api.app.dependency_overrides))]
    read = False

    async def body() -> AsyncIterator[bytes]:
        nonlocal read
        read = True
        yield b"--b\r\n"

    response = await api.client.post(
        DOCUMENTS, content=body(), headers={"content-type": "multipart/form-data; boundary=b"}
    )
    assert response.status_code == 401
    assert not read


async def test_the_limit_is_configured() -> None:
    container = build_memory_container()
    app = make_app(container, build_services(container), max_upload=10)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://papiq"
    ) as client:
        response = await client.post(
            DOCUMENTS, files=pdf(), headers={USER_HEADER: str(UUID(int=1))}
        )
    assert response.status_code == 413
