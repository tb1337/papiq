"""Milestone M6 end to end: documents arrive through the REST API, the worker processes and
indexes them, the search finds them for those who may read them. Real API server (Uvicorn),
worker and Meilisearch; fake embeddings (vectors of 16 numbers); OCR and parser are fakes that
bring the text of the test. Two set-ups: SQLite with the filesystem, Postgres with S3 (Garage)."""

import asyncio
import json
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx2
import pytest

from papiq.adapters.inbound.rest import PREFIX
from papiq.adapters.inbound.worker import Worker
from papiq.adapters.outbound.meilisearch import MeilisearchIndex
from papiq.adapters.outbound.memory import FakeEmbeddings
from papiq.adapters.outbound.sql import Database, SqlEventBus, SqlUnitOfWorkFactory
from papiq.composition.api import build_app
from papiq.composition.container import (
    Container,
    Services,
    build_memory_container,
    build_services,
)
from papiq.composition.settings import Settings
from papiq.core.domain.drawers import ShareLevel
from papiq.core.domain.users import Role, User
from papiq.core.ports import ObjectStore, OcrResult, ParseResult
from papiq.core.services.indexing import SUBSCRIBER
from tests import builders, probes
from tests.api import auth, issue_token, serving
from tests.integration.adapters.meilisearch.conftest import drop

DOCUMENTS = f"{PREFIX}/documents"
SEARCH = f"{DOCUMENTS}/search"
TIMEOUT = 60.0  # seconds a document may take here


class TextOcr:
    """OCR that keeps the file as it is: the archive holds the text line for the parser."""

    async def make_archive(self, source: Path, target: Path, *, media_type: str) -> OcrResult:
        target.write_bytes(source.read_bytes())
        return OcrResult(pages=1, engine="text-ocr 1", pdfa=True)


class TextParser:
    """A parser that takes the text of a document from a line of the file: `%text: …`."""

    async def parse(self, source: Path, *, markdown: Path, structure: Path) -> ParseResult:
        data = source.read_bytes()
        text = next(
            line.removeprefix(b"%text: ").decode()
            for line in data.splitlines()
            if line.startswith(b"%text: ")
        )
        markdown.write_text(text, encoding="utf-8")
        structure.write_text(json.dumps({"text": text}), encoding="utf-8")
        return ParseResult(pages=1, parser="text-parser 1")


def pdf(text: str) -> bytes:
    return f"%PDF-1.7\n%text: {text}\n%%EOF\n".encode()


@dataclass
class System:
    container: Container
    services: Services
    client: httpx2.AsyncClient
    settings: Settings
    index_name: str

    async def user(self, role: Role = Role.USER) -> User:
        user = builders.user(role=role)
        async with self.container.unit_of_work() as uow:
            await uow.users.add(user)
            await uow.drawers.add(builders.default_drawer(user))
            await uow.commit()
        await issue_token(self.services.auth, user)
        return user

    async def upload(self, user: User, text: str, **data: str) -> str:
        files = {"file": ("document.pdf", pdf(text), "application/pdf")}
        response = await self.client.post(DOCUMENTS, files=files, data=data, headers=auth(user))
        assert response.status_code == 202, response.text
        return str(response.json()["id"])

    async def search(self, user: User, text: str, **params: Any) -> dict[str, Any]:
        """By words unless the test asks for more: a search by meaning ranks every document, so
        it would find documents that are not about the text."""
        response = await self.client.get(
            SEARCH, params={"q": text, "semantic_ratio": 0, **params}, headers=auth(user)
        )
        assert response.status_code == 200, response.text
        page: dict[str, Any] = response.json()
        return page

    async def ids(self, user: User, text: str, **params: Any) -> set[str]:
        page = await self.search(user, text, **params)
        return {item["document"]["id"] for item in page["items"]}

    async def eventually(
        self, condition: Callable[[], Awaitable[bool]], what: str, timeout: float = TIMEOUT
    ) -> None:
        try:
            async with asyncio.timeout(timeout):
                while not await condition():
                    await asyncio.sleep(0.2)
        except TimeoutError:
            pytest.fail(f"timeout: {what}")

    async def found(self, user: User, text: str, expected: set[str], **params: Any) -> None:
        """Wait until the search finds exactly `expected` (the index follows a moment later)."""

        async def condition() -> bool:
            return await self.ids(user, text, **params) == expected

        await self.eventually(condition, f"{text!r} finds {expected}")


@pytest.fixture
async def system(stores: tuple[Database, ObjectStore], settings: Settings) -> AsyncIterator[System]:
    if settings.meilisearch_url is None:
        pytest.skip("PAPIQ_MEILISEARCH_URL is not set")
    if probes.meilisearch_health(str(settings.meilisearch_url)) is None:
        pytest.skip(f"Meilisearch not reachable at {settings.meilisearch_url}")
    database, object_store = stores
    name = f"papiq-test-{secrets.token_hex(6)}"
    key = settings.meilisearch_api_key
    index = MeilisearchIndex(
        url=str(settings.meilisearch_url),
        api_key=None if key is None else key.get_secret_value(),
        index=name,
        dimensions=FakeEmbeddings.DIMENSIONS,
    )
    container = replace(
        build_memory_container(),
        unit_of_work=SqlUnitOfWorkFactory(database),
        event_bus=SqlEventBus(database),
        object_store=object_store,
        ocr=TextOcr(),
        parser=TextParser(),
        search_index=index,
        embeddings=FakeEmbeddings(),
    )
    tuning = Settings.model_construct(
        worker_concurrency=2, events_poll_interval=timedelta(milliseconds=50)
    )
    services = build_services(container, tuning)
    builders.skip_classification(services.pipeline)
    assert services.indexing is not None
    container.event_bus.subscribe(SUBSCRIBER, services.indexing.on_event)
    worker = Worker(
        pipeline=services.pipeline,
        maintenance=services.maintenance,
        event_bus=container.event_bus,
        concurrency=2,
        poll_interval=timedelta(milliseconds=50),
        dispatch_interval=timedelta(milliseconds=50),
        shutdown_timeout=timedelta(seconds=30),
        indexing=services.indexing,
    )
    app = build_app(container, tuning, services)
    running = asyncio.create_task(worker.run())
    try:
        async with serving(app) as url, httpx2.AsyncClient(base_url=url, timeout=30) as client:
            yield System(container, services, client, settings, name)
    finally:
        worker.stop()
        await asyncio.wait_for(running, timeout=60)
        await index.aclose()
        await drop(settings, name, f"{name}-rebuild")


async def test_documents_are_found_by_those_who_may_read_them(system: System) -> None:
    owner, reader, stranger = await system.user(), await system.user(), await system.user()
    admin = await system.user(Role.ADMIN)
    async with system.container.unit_of_work() as uow:
        shared = builders.drawer(owner, "Household")
        shared.share(reader.id, ShareLevel.READ)
        await uow.drawers.add(shared)
        await uow.commit()

    bill = await system.upload(owner, "Stromrechnung der Stadtwerke", drawer_id=str(shared.id))
    lease = await system.upload(owner, "Mietvertrag für die Wohnung in Berlin")
    private = {bill, lease}

    # A new document is found by words soon after it has been filed.
    await system.found(owner, "Stromrechnung", {bill})
    await system.found(owner, "Mietvertrag", {lease})
    assert await system.ids(owner, "Wohnung Berlin") == {lease}
    assert await system.ids(owner, "document.pdf") == private  # the file name

    # Others find what is green in a drawer shared with them, nothing else.
    await system.found(reader, "Stromrechnung", {bill})  # once it is green, not while in processing
    assert await system.ids(reader, "Mietvertrag") == set()
    for user in (stranger, admin):
        assert await system.ids(user, "Stromrechnung") == set()
        assert await system.ids(user, "Mietvertrag") == set()

    # With embeddings, the meaning takes part and the rights hold for it as well.
    page = await system.search(reader, "Stromrechnung", semantic_ratio=1.0)
    assert page["semantic"] is True
    assert {item["document"]["id"] for item in page["items"]} <= {bill}
    page = await system.search(stranger, "Stromrechnung", semantic_ratio=1.0)
    assert page["items"] == []
    hit = (await system.search(owner, "Stromrechnung"))["items"][0]
    assert any(piece["match"] for piece in hit["snippet"])

    # Changes reach the index.
    patched = await system.client.patch(
        f"{DOCUMENTS}/{lease}", json={"title": "Wohnungsvertrag Hamburg"}, headers=auth(owner)
    )
    assert patched.status_code == 200, patched.text
    await system.found(owner, "Hamburg", {lease})

    # A withdrawn share ends the search at once, before the index could have changed.
    revoked = await system.client.delete(
        f"{PREFIX}/drawers/{shared.id}/shares/{reader.id}", headers=auth(owner)
    )
    assert revoked.status_code in {200, 204}, revoked.text
    assert await system.ids(reader, "Stromrechnung") == set()
    assert await system.ids(owner, "Stromrechnung") == {bill}

    # Deleting removes the document from the index.
    deleted = await system.client.delete(f"{DOCUMENTS}/{bill}", headers=auth(owner))
    assert deleted.status_code == 204
    await system.found(owner, "Stromrechnung", set())

    health = await system.client.get(f"{PREFIX}/health")
    assert health.json()["status"] == "ok"


async def test_the_index_is_rebuilt_on_an_empty_meilisearch(system: System) -> None:
    owner, admin = await system.user(), await system.user(Role.ADMIN)
    first = await system.upload(owner, "Steuerbescheid Finanzamt")
    second = await system.upload(owner, "Versicherungspolice Hausrat")
    await system.found(owner, "Steuerbescheid", {first})
    await system.found(owner, "Versicherungspolice", {second})

    # Meilisearch loses everything.
    await drop(system.settings, system.index_name)

    async def gone() -> bool:
        """The search fails (503) or, once the adapter has set up an empty index, finds nothing."""
        response = await system.client.get(
            SEARCH, params={"q": "Steuerbescheid", "semantic_ratio": 0}, headers=auth(owner)
        )
        return response.status_code == 503 or response.json()["items"] == []

    await system.eventually(gone, "the loss shows", timeout=10)

    denied = await system.client.post(f"{PREFIX}/search/reindex", headers=auth(owner))
    assert denied.status_code == 403
    rebuild = await system.client.post(f"{PREFIX}/search/reindex", headers=auth(admin))
    assert rebuild.status_code == 202

    await system.found(owner, "Steuerbescheid", {first})
    await system.found(owner, "Versicherungspolice", {second})
    page = await system.search(owner, "Steuerbescheid", semantic_ratio=0.5)
    assert page["semantic"] is True  # the vectors were made again
