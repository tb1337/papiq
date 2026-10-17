"""Milestone M3 end to end: documents arrive through the REST API, the worker processes them
with OCRmyPDF and the layout text, progress arrives as server-sent events. Real API server
(Uvicorn) and worker, in two set-ups: SQLite with the filesystem, Postgres with S3 (Garage)."""

import asyncio
import shutil
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx2
import pytest

from papiq.adapters.inbound.rest import PREFIX
from papiq.adapters.inbound.worker import Worker
from papiq.adapters.outbound.ocrmypdf import OcrmypdfEngine
from papiq.adapters.outbound.pdfium import LayoutParser, PdfiumPageRenderer
from papiq.adapters.outbound.sql import Database, SqlEventBus, SqlUnitOfWorkFactory
from papiq.composition.api import build_app
from papiq.composition.container import (
    Container,
    Services,
    build_memory_container,
    build_services,
)
from papiq.composition.settings import Settings
from papiq.core.domain.spaces import ShareLevel
from papiq.core.domain.users import User
from papiq.core.ports import ObjectStore, OcrResult
from papiq.core.ports.ocr import VisionModel
from papiq.core.services.objects import archive_key, preview_key, structure_key, text_key
from tests import builders
from tests.api import Stream, auth, issue_token, listen, serving, until
from tests.contracts.processing import SAMPLES

if not (shutil.which("tesseract") and shutil.which("gs")):
    pytest.skip("Tesseract or Ghostscript not installed", allow_module_level=True)

DOCUMENTS = f"{PREFIX}/documents"
PROCESSING = 180.0  # seconds a document may take here


class Interruptible:
    """OCR that can be made to time out, to break a node on purpose."""

    def __init__(self, engine: OcrmypdfEngine) -> None:
        self.engine = engine
        self.broken = False

    @property
    def vision(self) -> VisionModel | None:
        return self.engine.vision

    async def make_archive(
        self, source: Path, target: Path, *, media_type: str, vision: bool = False
    ) -> OcrResult:
        if self.broken:
            raise TimeoutError("ocrmypdf did not finish within 0.001 s")
        return await self.engine.make_archive(source, target, media_type=media_type, vision=vision)


@dataclass
class System:
    container: Container
    services: Services
    ocr: Interruptible
    client: httpx2.AsyncClient

    async def user(self) -> User:
        user = builders.user()
        async with self.container.unit_of_work() as uow:
            await uow.users.add(user)
            await uow.spaces.add(builders.default_space(user))
            await uow.commit()
        await issue_token(self.services.auth, user)
        return user

    async def upload(self, user: User, sample: str, **data: str) -> httpx2.Response:
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


@pytest.fixture
async def system(stores: tuple[Database, ObjectStore], settings: Settings) -> AsyncIterator[System]:
    database, object_store = stores
    ocr = Interruptible(OcrmypdfEngine(languages=["deu", "eng"], timeout=timedelta(minutes=5)))
    container = replace(
        build_memory_container(),
        unit_of_work=SqlUnitOfWorkFactory(database),
        event_bus=SqlEventBus(database),
        object_store=object_store,
        ocr=ocr,
        parser=LayoutParser(),
        pages=PdfiumPageRenderer(),
    )
    tuning = Settings.model_construct(
        node_max_attempts=1,  # a broken node fails at once
        worker_concurrency=2,
        events_poll_interval=timedelta(milliseconds=50),
    )
    services = build_services(container, tuning)
    builders.skip_classification(services.pipeline)
    worker = Worker(
        pipeline=services.pipeline,
        maintenance=services.maintenance,
        event_bus=container.event_bus,
        concurrency=2,
        poll_interval=timedelta(milliseconds=50),
        dispatch_interval=timedelta(milliseconds=50),
        shutdown_timeout=timedelta(seconds=30),
    )
    app = build_app(container, tuning, services)
    running = asyncio.create_task(worker.run())
    try:
        async with serving(app) as url, httpx2.AsyncClient(base_url=url, timeout=30) as client:
            yield System(container, services, ocr, client)
    finally:
        worker.stop()
        await asyncio.wait_for(running, timeout=60)  # the worker shuts down cleanly


async def test_documents_go_through_the_api(system: System, object_store: ObjectStore) -> None:
    owner, reader, stranger = await system.user(), await system.user(), await system.user()
    async with system.container.unit_of_work() as uow:
        shared = builders.space(owner, "Household")
        shared.share(reader.id, ShareLevel.READ)
        await uow.spaces.add(shared)
        await uow.commit()

    streams = {user.id: Stream() for user in (owner, reader, stranger)}
    listeners = [
        asyncio.create_task(listen(system.client, user, streams[user.id]))
        for user in (owner, reader, stranger)
    ]
    for stream in streams.values():
        await asyncio.wait_for(stream.ready.wait(), timeout=10)
    await asyncio.sleep(0.1)

    # A scan into the shared space and a photo into the owner's default space.
    scan = await system.upload(owner, "scan.pdf", space_id=str(shared.id))
    photo = await system.upload(owner, "photo.jpg")
    assert (scan.status_code, photo.status_code) == (202, 202)
    ids = {"scan": scan.json()["id"], "photo": photo.json()["id"]}
    for name, key in ids.items():
        status = await system.settled(owner, key)
        assert status["lane"] == "green", (name, status)
        assert await object_store.get(archive_key(key))
        assert (await object_store.get(preview_key(key)))[8:12] == b"WEBP"
        text = " ".join((await object_store.get(text_key(key))).decode().split())
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

    # A node broken on purpose fails, is retried alone, and reprocessing works.
    system.ocr.broken = True
    born_digital = await system.upload(owner, "text.pdf")
    url, id = born_digital.json()["status_url"], born_digital.json()["id"]
    failed = await system.settled(owner, id)
    outcomes = {n["node"]: n["outcome"] for n in failed["processing"]["nodes"]}
    assert (failed["lane"], outcomes["ocr"]) == ("red", "failed")
    system.ocr.broken = False
    retried = await system.client.post(f"{url}/retry", headers=auth(owner))
    assert retried.status_code == 202
    assert (await system.settled(owner, id))["lane"] == "green"
    reprocess = await system.client.post(
        f"{url}/reprocess", json={"from_node": "parse"}, headers=auth(owner)
    )
    assert reprocess.status_code == 202
    assert (await system.settled(owner, id))["lane"] == "green"
    log = (await system.client.get(f"{url}/log", headers=auth(owner))).json()
    runs = [(entry["node"], entry["run"], entry["outcome"]) for entry in log]
    # The pages run beside the OCR: in the first run if they got there before it failed, else
    # after the retry, before or after the OCR. Their place in the log is a matter of timing.
    # The signals, too, run side by side; the judge comes after all of them.
    chain = [run for run in runs if run[0] != "pages"]
    assert chain[:4] == [
        ("receive", 1, "ok"),
        ("ocr", 1, "failed"),
        ("ocr", 2, "ok"),
        ("parse", 2, "ok"),
    ]
    signals = {"signal_llm", "signal_classifier", "signal_neighbours", "signal_facts"}
    assert {node for node, _, _ in chain[4:8]} == signals
    assert chain[8] == ("judge", 2, "ok")
    assert [outcome for node, _, outcome in runs if node == "pages"][:1] == ["ok"]
    assert ("parse", 3, "ok") in runs

    health = await system.client.get(f"{PREFIX}/health")
    assert health.json()["status"] == "ok"
    for listener in listeners:
        listener.cancel()
