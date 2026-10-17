"""Upload, status, processing log, retry and reprocessing over HTTP."""

import json
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx2
import pytest

from papiq.adapters.inbound.rest import PREFIX
from papiq.composition.container import build_memory_container, build_services
from papiq.core.domain.documents import Sha256
from papiq.core.domain.errors import UnprocessableDocumentError
from papiq.core.domain.exchanges import ModelCall, RunExchange, exchanges_to_json
from papiq.core.domain.ids import DocumentId
from papiq.core.domain.pipeline import Node
from papiq.core.domain.spaces import ShareLevel
from papiq.core.services.objects import exchanges_key, original_key
from tests.api import auth, issue_token
from tests.builders import PASSWORD
from tests.contracts.processing import SAMPLES
from tests.unit.adapters.rest.conftest import MAX_UPLOAD, Api, make_app

DOCUMENTS = f"{PREFIX}/documents"
SIGNALS = ("signal_llm", "signal_classifier", "signal_neighbours", "signal_facts")
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
    processing = status["processing"]
    assert (processing["status"], processing["run"]) == ("processing", 1)
    assert [node["node"] for node in processing["nodes"]] == [
        "receive",
        "ocr",
        "pages",
        "parse",
        "signal_llm",
        "signal_classifier",
        "signal_neighbours",
        "signal_facts",
        "judge",
        "fields",
        "apply_rules",
        "file",
    ]
    assert processing["nodes"][0] == {
        "node": "receive",
        "status": "done",
        "outcome": "ok",
        "started_at": "2026-10-06T12:00:00Z",
        "finished_at": "2026-10-06T12:00:00Z",
    }
    assert processing["nodes"][1] == {
        "node": "ocr",
        "status": "waiting",
        "outcome": None,
        "started_at": None,
        "finished_at": None,
    }

    await api.drain()
    status = (await api.client.get(f"{DOCUMENTS}/{id}", headers=auth(owner))).json()
    assert (status["lane"], status["processing"]["status"]) == ("green", "completed")
    log = (await api.client.get(f"{DOCUMENTS}/{id}/log", headers=auth(owner))).json()
    assert [entry["node"] for entry in log][:3] in (
        ["receive", "ocr", "pages"],
        ["receive", "pages", "ocr"],
        ["receive", "ocr", "parse"],
    )
    assert log[0]["output"]["sha256"]
    ocr = next(entry for entry in log if entry["node"] == "ocr")
    assert ocr["model_version"] == "fake-ocr 1"


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


async def test_fields_count_against_the_limit(api: Api) -> None:
    """Many fields in a chunked body are neither kept nor read without end."""
    owner = await api.user()
    boundary = "papiq-test-boundary"
    sent = 0

    async def body() -> AsyncIterator[bytes]:
        nonlocal sent
        for number in range(100_000):
            part = (
                f'--{boundary}\r\nContent-Disposition: form-data; name="f{number}"\r\n\r\n'
                f"{'x' * 100}\r\n"
            ).encode()
            sent += len(part)
            yield part
        yield f"--{boundary}--\r\n".encode()

    response = await api.client.post(
        DOCUMENTS,
        content=body(),
        headers={**auth(owner), "content-type": f"multipart/form-data; boundary={boundary}"},
    )
    assert response.status_code == 413
    assert sent < 2 * MAX_UPLOAD


@pytest.mark.parametrize(
    ("kwargs", "status", "detail"),
    [
        ({"files": {"other": ("a.pdf", b"%PDF-1.7")}}, 400, "no file part"),
        (
            {"content": b"%PDF-1.7", "headers": {"content-type": "application/pdf"}},
            400,
            "multipart",
        ),
        ({"files": pdf(), "data": {"space_id": "no-uuid"}}, 422, "space_id"),
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


async def test_upload_into_a_space(api: Api) -> None:
    owner, writer, reader = await api.user(), await api.user(), await api.user()
    spaces = api.services  # space service lives in the services, not in the API (M4)
    shared = await spaces.spaces.create(owner.id, "Household")
    await spaces.spaces.share(owner.id, shared.id, writer.id, ShareLevel.READ_WRITE)
    await spaces.spaces.share(owner.id, shared.id, reader.id, ShareLevel.READ)
    data = {"space_id": str(shared.id)}

    accepted = await api.client.post(DOCUMENTS, files=pdf(), data=data, headers=auth(writer))
    assert accepted.status_code == 202
    status = await api.client.get(accepted.json()["status_url"], headers=auth(writer))
    assert status.json()["space_id"] == str(shared.id)
    denied = await api.client.post(DOCUMENTS, files=pdf(), data=data, headers=auth(reader))
    assert denied.status_code == 403
    assert denied.json()["space_id"] == str(shared.id)
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


async def test_a_filename_may_hold_any_letter(api: Api) -> None:
    owner = await api.user()
    name = "Ertr\u00e4gnisaufstellung \u2013 2024 \u20ac.pdf"  # beyond Latin-1
    files = {"file": (name, (SAMPLES / "scan.pdf").read_bytes(), "x/y")}
    response = await api.client.post(DOCUMENTS, files=files, headers=auth(owner))
    assert response.status_code == 202, response.text
    status = await api.client.get(response.json()["status_url"], headers=auth(owner))
    assert status.json()["original_filename"] == name
    assert status.json()["title"] == "Ertr\u00e4gnisaufstellung \u2013 2024 \u20ac"


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
    shared = await api.services.spaces.create(owner.id, "Shared")
    await api.services.spaces.share(owner.id, shared.id, reader.id, ShareLevel.READ)
    response = await api.client.post(
        DOCUMENTS, files=pdf(), data={"space_id": str(shared.id)}, headers=auth(owner)
    )
    url = response.json()["status_url"]
    assert (await api.client.get(url, headers=auth(reader))).status_code == 404  # processing
    await api.drain()
    assert (await api.client.get(url, headers=auth(reader))).status_code == 200
    assert (await api.client.get(f"{url}/log", headers=auth(reader))).status_code == 403
    for path in (url, f"{url}/log"):
        missing = await api.client.get(path, headers=auth(stranger))
        assert missing.status_code == 404
        assert missing.headers["content-type"] == PROBLEM


async def test_the_text_is_read_in_pieces(api: Api) -> None:
    owner = await api.user()
    response = await api.client.post(DOCUMENTS, files=pdf(), headers=auth(owner))
    url = response.json()["status_url"]
    assert (await api.client.get(f"{url}/text", headers=auth(owner))).status_code == 409
    await api.drain()
    whole = (await api.client.get(f"{url}/text", headers=auth(owner))).json()
    text = whole["text"]
    assert text and whole == {
        "text": text,
        "offset": 0,
        "total_length": len(text),
        "next_offset": None,
    }
    piece = await api.client.get(f"{url}/text?offset=2&limit=3", headers=auth(owner))
    assert piece.json() == {
        "text": text[2:5],
        "offset": 2,
        "total_length": len(text),
        "next_offset": 5 if len(text) > 5 else None,
    }
    assert (await api.client.get(f"{url}/text?limit=0", headers=auth(owner))).status_code == 422
    assert (await api.client.get(f"{url}/text")).status_code == 401
    other = await api.user()
    assert (await api.client.get(f"{url}/text", headers=auth(other))).status_code == 404


async def test_the_structure_is_served_as_stored(api: Api) -> None:
    owner = await api.user()
    response = await api.client.post(DOCUMENTS, files=pdf(), headers=auth(owner))
    url = response.json()["status_url"]
    assert (await api.client.get(f"{url}/structure", headers=auth(owner))).status_code == 409
    await api.drain()
    structure = await api.client.get(f"{url}/structure", headers=auth(owner))
    assert structure.status_code == 200
    assert structure.headers["content-type"] == "application/json"
    assert structure.headers["x-content-type-options"] == "nosniff"
    assert "text" in structure.json()  # what the fake parser writes


async def test_the_model_exchanges_are_listed_per_run(api: Api) -> None:
    owner = await api.user()
    response = await api.client.post(DOCUMENTS, files=pdf(), headers=auth(owner))
    url = response.json()["status_url"]
    await api.drain()
    # Without a language model nothing was sent.
    listed = await api.client.get(f"{url}/exchanges/signal_llm", headers=auth(owner))
    assert (listed.status_code, listed.json()) == (200, [])
    id = DocumentId(UUID(url.rsplit("/", 1)[-1]))
    call = ModelCall(system="s", user="u", answer="{}", model="m", usage={"total_tokens": 9})
    runs = [RunExchange(run=1, calls=(call,))]
    await api.container.object_store.put(
        exchanges_key(id, Node.SIGNAL_LLM),
        json.dumps(exchanges_to_json(runs)).encode(),
        content_type="application/json",
    )
    listed = await api.client.get(f"{url}/exchanges/signal_llm", headers=auth(owner))
    assert listed.json() == [
        {
            "run": 1,
            "calls": [
                {
                    "system": "s",
                    "user": "u",
                    "answer": "{}",
                    "model": "m",
                    "usage": {"total_tokens": 9},
                }
            ],
        }
    ]
    wrong = await api.client.get(f"{url}/exchanges/parse", headers=auth(owner))
    assert wrong.status_code == 422


async def test_page_images_are_served_by_number(api: Api) -> None:
    owner = await api.user()
    response = await api.client.post(DOCUMENTS, files=pdf(), headers=auth(owner))
    url = response.json()["status_url"]
    assert (await api.client.get(f"{url}/pages/1", headers=auth(owner))).status_code == 404
    await api.drain()
    document = (await api.client.get(url, headers=auth(owner))).json()
    assert document["page_count"] == 1
    page = await api.client.get(f"{url}/pages/1", headers=auth(owner))
    assert page.status_code == 200
    assert page.headers["content-type"] == "image/webp"
    assert page.headers["content-disposition"].startswith("inline")
    assert page.content[:4] == b"RIFF"
    assert (await api.client.get(f"{url}/pages/2", headers=auth(owner))).status_code == 404
    assert (await api.client.get(f"{url}/pages/0", headers=auth(owner))).status_code == 422
    assert (await api.client.get(f"{url}/pages/1")).status_code == 401


async def test_retry_and_reprocess(api: Api) -> None:
    class Broken:
        async def run(self, document: object) -> object:
            raise UnprocessableDocumentError("the PDF is encrypted")

    owner = await api.user()
    executors = api.services.pipeline._executors
    working = executors[Node.OCR]
    executors[Node.OCR] = Broken()  # type: ignore[assignment]
    response = await api.client.post(DOCUMENTS, files=pdf(), headers=auth(owner))
    url = response.json()["status_url"]
    await api.drain()
    failed = (await api.client.get(url, headers=auth(owner))).json()
    assert (failed["lane"], failed["processing"]["status"]) == ("red", "failed")

    # Reprocessing must not skip the failed node.
    skip = await api.client.post(
        f"{url}/reprocess", json={"from_node": "parse"}, headers=auth(owner)
    )
    assert skip.status_code == 409

    executors[Node.OCR] = working
    retried = await api.client.post(f"{url}/retry", headers=auth(owner))
    assert retried.status_code == 202
    processing = retried.json()["processing"]
    assert (processing["status"], processing["run"]) == ("processing", 2)
    assert nodes(processing) == {
        "receive": "done",
        "pages": nodes(failed["processing"])["pages"],  # done or dropped: the branches race
        **waiting("ocr", "parse", *SIGNALS, "judge", "fields", "apply_rules", "file"),
    }
    again = await api.client.post(f"{url}/retry", headers=auth(owner))
    assert again.status_code == 409  # running, not failed
    await api.drain()
    assert (await api.client.get(url, headers=auth(owner))).json()["lane"] == "green"

    reprocessed = await api.client.post(
        f"{url}/reprocess", json={"from_node": "parse"}, headers=auth(owner)
    )
    assert reprocessed.status_code == 202
    assert nodes(reprocessed.json()["processing"])["parse"] == "waiting"
    assert nodes(reprocessed.json()["processing"])["ocr"] == "done"
    await api.drain()
    log = (await api.client.get(f"{url}/log", headers=auth(owner))).json()
    runs = [(entry["node"], entry["run"]) for entry in log if entry["node"] in ("ocr", "parse")]
    assert runs == [("ocr", 1), ("ocr", 2), ("parse", 2), ("parse", 3)]


async def test_reprocess_validates_the_step(api: Api) -> None:
    owner = await api.user()
    response = await api.client.post(DOCUMENTS, files=pdf(), headers=auth(owner))
    url = response.json()["status_url"]
    await api.drain()
    for body in ({"from_node": "receive"}, {"from_node": "nope"}, {}):
        invalid = await api.client.post(f"{url}/reprocess", json=body, headers=auth(owner))
        assert invalid.status_code == 422, body
        assert invalid.headers["content-type"] == PROBLEM
        assert "from_node" in invalid.json()["detail"]


async def test_only_the_owner_controls_processing(api: Api) -> None:
    owner, writer = await api.user(), await api.user()
    shared = await api.services.spaces.create(owner.id, "Shared")
    await api.services.spaces.share(owner.id, shared.id, writer.id, ShareLevel.READ_WRITE)
    response = await api.client.post(
        DOCUMENTS, files=pdf(), data={"space_id": str(shared.id)}, headers=auth(owner)
    )
    url = response.json()["status_url"]
    await api.drain()
    denied = await api.client.post(
        f"{url}/reprocess", json={"from_node": "ocr"}, headers=auth(writer)
    )
    assert denied.status_code == 403
    missing = await api.client.post(f"{DOCUMENTS}/{UUID(int=0)}/retry", headers=auth(owner))
    assert missing.status_code == 404


async def test_unauthenticated_uploads_are_not_read(api: Api) -> None:
    """The user is checked before the body is received."""
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
    services = build_services(container)
    app = make_app(container, services, max_upload=10)
    owner = await services.users.bootstrap_admin("root", PASSWORD)
    assert owner is not None
    await issue_token(services.auth, owner)
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://papiq"
    ) as client:
        response = await client.post(DOCUMENTS, files=pdf(), headers=auth(owner))
    assert response.status_code == 413


async def test_unexpected_errors_are_problems_without_details(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(*args: object, **kwargs: object) -> object:
        raise OSError("s3://secret-bucket/originals unreachable")

    monkeypatch.setattr(api.services.documents, "view", broken)
    owner = await api.user()
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=api.app, raise_app_exceptions=False),
        base_url="http://papiq",
    ) as client:
        response = await client.get(f"{DOCUMENTS}/{UUID(int=1)}", headers=auth(owner))
    assert response.status_code == 500
    assert response.headers["content-type"] == PROBLEM
    assert response.json()["detail"] == "an unexpected error occurred"
    assert "secret" not in response.text


async def test_uploads_have_their_own_limit() -> None:
    """M4-04: the general limit for request bodies (1 MiB) does not apply to uploads."""
    container = build_memory_container()
    services = build_services(container)
    app = make_app(container, services, max_upload=2 * 1024 * 1024)
    owner = await services.users.bootstrap_admin("root", PASSWORD)
    assert owner is not None
    await issue_token(services.auth, owner)
    content = b"%PDF-1.7\n%" + b"0" * (2 * 1024 * 1024 - 64 * 1024) + b"\n%%EOF\n"
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://papiq"
    ) as client:
        files = {"file": ("big.pdf", content, "application/pdf")}
        response = await client.post(DOCUMENTS, files=files, headers=auth(owner))
    assert response.status_code == 202, response.text


def nodes(processing: dict[str, Any]) -> dict[str, str]:
    """The status of each node, by node."""
    return {node["node"]: node["status"] for node in processing["nodes"]}


def waiting(*names: str) -> dict[str, str]:
    return dict.fromkeys(names, "waiting")
