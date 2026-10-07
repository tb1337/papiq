"""Milestone M3 end to end: documents arrive through the REST API, the worker processes them
with OCRmyPDF and Docling, progress arrives as server-sent events. Real API server (Uvicorn)
and worker, in two set-ups: SQLite with the filesystem, Postgres with S3 (Garage).
Marker `docling` (slow)."""

import asyncio
import secrets
import shutil
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from papiq.adapters.inbound.rest import PREFIX
from papiq.adapters.inbound.worker import Worker
from papiq.adapters.outbound.docling import DoclingParser
from papiq.adapters.outbound.filesystem import FilesystemObjectStore
from papiq.adapters.outbound.ocrmypdf import OcrmypdfEngine
from papiq.adapters.outbound.pdfium import PdfiumPreviewRenderer
from papiq.adapters.outbound.sql import Database, SqlEventBus, SqlUnitOfWorkFactory, migrate
from papiq.composition.api import build_app
from papiq.composition.container import Container, build_memory_container, build_services
from papiq.composition.settings import Settings
from papiq.core.domain.drawers import ShareLevel
from papiq.core.domain.users import User
from papiq.core.ports import ObjectStore, OcrResult
from papiq.core.services.objects import archive_key, markdown_key, preview_key, structure_key
from tests import builders, probes
from tests.api import Stream, allow_test_users, auth, listen, serving, until
from tests.contracts.processing import SAMPLES
from tests.integration.adapters.sql.conftest import create_database, drop_database, postgres
from tests.integration.conftest import s3_test_store

pytestmark = pytest.mark.docling

if not (shutil.which("tesseract") and shutil.which("gs")):
    pytest.skip("Tesseract or Ghostscript not installed", allow_module_level=True)

DOCUMENTS = f"{PREFIX}/documents"
PROCESSING = 180.0  # seconds a document may take here


class Interruptible:
    """OCR that can be made to time out, to break a step on purpose."""

    def __init__(self, engine: OcrmypdfEngine) -> None:
        self.engine = engine
        self.broken = False

    async def make_archive(self, source: Path, target: Path, *, media_type: str) -> OcrResult:
        if self.broken:
            raise TimeoutError("ocrmypdf did not finish within 0.001 s")
        return await self.engine.make_archive(source, target, media_type=media_type)


@dataclass
class System:
    container: Container
    ocr: Interruptible
    client: httpx.AsyncClient

    async def user(self) -> User:
        user = builders.user()
        async with self.container.unit_of_work() as uow:
            await uow.users.add(user)
            await uow.drawers.add(builders.default_drawer(user))
            await uow.commit()
        return user

    async def upload(self, user: User, sample: str, **data: str) -> httpx.Response:
        files = {"file": (sample, (SAMPLES / sample).read_bytes(), "application/octet-stream")}
        return await self.client.post(DOCUMENTS, files=files, data=data, headers=auth(user))

    async def status(self, user: User, id: str) -> dict[str, Any]:
        response = await self.client.get(f"{DOCUMENTS}/{id}", headers=auth(user))
        assert response.status_code == 200, response.text
        body: dict[str, Any] = response.json()
        return body

    async def settled(self, user: User, id: str) -> dict[str, Any]:
        """The status once processing has ended."""
        async with asyncio.timeout(PROCESSING):
            while (status := await self.status(user, id))["lane"] is None:
                await asyncio.sleep(0.2)
        return status


@pytest.fixture(params=["sqlite+filesystem", "postgres+s3"])
async def stores(
    request: pytest.FixtureRequest, tmp_path: Path
) -> AsyncIterator[tuple[Database, ObjectStore]]:
    if request.param == "sqlite+filesystem":
        database = Database.sqlite(tmp_path / "papiq.db")
        await migrate(database)
        yield database, FilesystemObjectStore(tmp_path / "objects")
        await database.dispose()
        return
    settings: Settings = request.getfixturevalue("settings")
    if settings.db_type != "postgres" or settings.db_host is None:
        pytest.skip("PAPIQ_DB_TYPE is not postgres")
    if not probes.postgres_answers(settings.db_host, settings.db_port):
        pytest.skip(f"Postgres not reachable at {settings.db_host}:{settings.db_port}")
    s3: Settings = request.getfixturevalue("s3_settings")
    assert settings.db_name
    name = f"{settings.db_name}_test_{secrets.token_hex(4)}"
    await create_database(settings, name)
    database = postgres(settings, name)
    try:
        await migrate(database)
        async with s3_test_store(s3) as store:
            yield database, store
    finally:
        await database.dispose()
        await drop_database(settings, name)


@pytest.fixture
def object_store(stores: tuple[Database, ObjectStore]) -> ObjectStore:
    return stores[1]


@pytest.fixture
async def system(stores: tuple[Database, ObjectStore], settings: Settings) -> AsyncIterator[System]:
    if not settings.docling_models_path.is_dir():
        pytest.skip(f"Docling models not found in {settings.docling_models_path}")
    database, object_store = stores
    ocr = Interruptible(OcrmypdfEngine(languages=["deu", "eng"], timeout=timedelta(minutes=5)))
    container = replace(
        build_memory_container(),
        unit_of_work=SqlUnitOfWorkFactory(database),
        event_bus=SqlEventBus(database),
        object_store=object_store,
        ocr=ocr,
        parser=DoclingParser(models=settings.docling_models_path, timeout=timedelta(minutes=5)),
        previews=PdfiumPreviewRenderer(),
    )
    tuning = Settings.model_construct(
        step_max_attempts=1,  # a broken step fails at once
        worker_concurrency=2,
        events_poll_interval=timedelta(milliseconds=50),
    )
    services = build_services(container, tuning)
    worker = Worker(
        pipeline=services.pipeline,
        maintenance=services.maintenance,
        event_bus=container.event_bus,
        concurrency=2,
        poll_interval=timedelta(milliseconds=50),
        dispatch_interval=timedelta(milliseconds=50),
        shutdown_timeout=timedelta(seconds=30),
    )
    app = allow_test_users(build_app(container, tuning))
    running = asyncio.create_task(worker.run())
    try:
        async with serving(app) as url, httpx.AsyncClient(base_url=url, timeout=30) as client:
            yield System(container, ocr, client)
    finally:
        worker.stop()
        await asyncio.wait_for(running, timeout=60)  # the worker shuts down cleanly


async def test_documents_go_through_the_api(system: System, object_store: ObjectStore) -> None:
    owner, reader, stranger = await system.user(), await system.user(), await system.user()
    async with system.container.unit_of_work() as uow:
        shared = builders.drawer(owner, "Household")
        shared.share(reader.id, ShareLevel.READ)
        await uow.drawers.add(shared)
        await uow.commit()

    streams = {user.id: Stream() for user in (owner, reader, stranger)}
    listeners = [
        asyncio.create_task(listen(system.client, user, streams[user.id]))
        for user in (owner, reader, stranger)
    ]
    for stream in streams.values():
        await asyncio.wait_for(stream.ready.wait(), timeout=10)
    await asyncio.sleep(0.1)

    # A scan into the shared drawer and a photo into the owner's default drawer.
    scan = await system.upload(owner, "scan.pdf", drawer_id=str(shared.id))
    photo = await system.upload(owner, "photo.jpg")
    assert (scan.status_code, photo.status_code) == (202, 202)
    ids = {"scan": scan.json()["id"], "photo": photo.json()["id"]}
    for name, key in ids.items():
        status = await system.settled(owner, key)
        assert status["lane"] == "green", (name, status)
        assert await object_store.get(archive_key(key))
        assert (await object_store.get(preview_key(key)))[8:12] == b"WEBP"
        text = " ".join((await object_store.get(markdown_key(key))).decode().split())
        assert "Rechnung Nummer 4711" in text, name
        assert await object_store.exists(structure_key(key))

    # A duplicate of the same owner is rejected.
    again = await system.upload(owner, "scan.pdf")
    assert again.status_code == 409
    assert again.json()["existing_document_id"] == ids["scan"]

    # Progress by SSE, only for those who may read the document.
    own = streams[owner.id]
    await until(lambda: own.types().count("document.lane_changed") == 2, timeout=10)
    assert own.documents() == set(ids.values())
    lanes = {e["document_id"]: e["new"] for e in own.events if e["type"] == "document.lane_changed"}
    assert lanes == dict.fromkeys(ids.values(), "green")
    shared_stream = streams[reader.id]
    await until(lambda: "document.lane_changed" in shared_stream.types(), timeout=10)
    assert shared_stream.documents() == {ids["scan"]}
    assert "document.received" not in shared_stream.types()  # hidden while processing
    assert streams[stranger.id].events == []

    # A step broken on purpose fails, is retried alone, and reprocessing works.
    system.ocr.broken = True
    born_digital = await system.upload(owner, "text.pdf")
    url, id = born_digital.json()["status_url"], born_digital.json()["id"]
    failed = await system.settled(owner, id)
    assert (failed["lane"], failed["processing"]["current_step"]) == ("red", "ocr")
    system.ocr.broken = False
    retried = await system.client.post(f"{url}/retry", headers=auth(owner))
    assert retried.status_code == 202
    assert (await system.settled(owner, id))["lane"] == "green"
    reprocess = await system.client.post(
        f"{url}/reprocess", json={"from_step": "parse"}, headers=auth(owner)
    )
    assert reprocess.status_code == 202
    assert (await system.settled(owner, id))["lane"] == "green"
    log = (await system.client.get(f"{url}/log", headers=auth(owner))).json()
    runs = [(entry["step"], entry["run"], entry["outcome"]) for entry in log][:5]
    assert runs == [
        ("receive", 1, "ok"),
        ("ocr", 1, "failed"),
        ("ocr", 2, "ok"),
        ("parse", 2, "ok"),
        ("classify", 2, "ok"),
    ]
    assert ("parse", 3, "ok") in [(entry["step"], entry["run"], entry["outcome"]) for entry in log]

    health = await system.client.get(f"{PREFIX}/health")
    assert health.json()["status"] == "ok"
    for listener in listeners:
        listener.cancel()
