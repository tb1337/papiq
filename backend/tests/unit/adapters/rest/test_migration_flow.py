"""The migration client against the real API (in memory), with a stand-in for Paperless: the two
sides agree on every endpoint, field and rule the client relies on."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx2
from papiq_migration import fake_paperless
from papiq_migration.cli import execute
from papiq_migration.config import Config
from papiq_migration.fake_paperless import FakePaperless, sample_archive

from papiq.adapters.inbound.rest import PREFIX
from papiq.core.domain.pipeline import Step
from papiq.core.services.imports import ImportedClassifyStep, ImportedExtractStep
from papiq.core.services.pipeline import PlaceholderStep
from tests import api as helpers
from tests.contracts.processing import SAMPLES
from tests.unit.adapters.rest.conftest import Api


class Migrating:
    def __init__(self, api: Api, directory: Path, admin: Any) -> None:
        self.api = api
        self.directory = directory
        self.admin = admin
        archive = sample_archive(
            (SAMPLES / "scan.pdf").read_bytes(), (SAMPLES / "photo.jpg").read_bytes()
        )
        self.paperless = FakePaperless(archive)
        self.messages: list[str] = []

    def config(self, **changes: Any) -> Config:
        values: dict[str, Any] = {
            "paperless_url": "http://paperless",
            "papiq_url": "https://papiq",
            "state": self.directory / "state.sqlite",
            "report_dir": self.directory / "reports",
            "paperless_token": fake_paperless.TOKEN,
            "papiq_token": helpers._TOKENS[self.admin.id],
            "concurrency": 3,
        }
        return Config(**{**values, **changes})

    async def run(self, command: str, *, stop: asyncio.Event | None = None, **changes: Any) -> int:
        return await execute(
            self.config(**changes),
            command,
            stop=stop,
            paperless_transport=self.paperless.transport(),
            papiq_transport=httpx2.ASGITransport(app=self.api.app),
            progress=self.messages.append,
            poll=(0.01, 0.01),
            delays=(0.0, 0.0, 0.0),
        )

    async def documents(self) -> list[dict[str, Any]]:
        response = await self.api.client.get(
            f"{PREFIX}/documents?all_users=true&limit=200", headers=helpers.auth(self.admin)
        )
        assert response.status_code == 200
        return list(response.json()["items"])


@asynccontextmanager
async def migrating(api: Api, directory: Path) -> AsyncIterator[Migrating]:
    """The API with its worker running, an admin to migrate with, and the real classification
    steps around steps that do nothing (there is no language model)."""
    uow = api.container.unit_of_work
    executors = api.services.pipeline._executors
    executors[Step.CLASSIFY] = ImportedClassifyStep(uow, PlaceholderStep())
    executors[Step.EXTRACT_FIELDS] = ImportedExtractStep(uow, PlaceholderStep())
    admin = await api.admin()

    async def work() -> None:
        while True:
            await api.drain()
            await asyncio.sleep(0.005)

    worker = asyncio.create_task(work())
    try:
        yield Migrating(api, directory, admin)
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)


async def test_the_whole_migration_runs_against_the_api(api: Api, tmp_path: Path) -> None:
    async with migrating(api, tmp_path) as m:
        assert await m.run("plan") == 0
        assert await m.documents() == []

        assert await m.run("run") == 0, m.messages
        documents = await m.documents()
        assert len(documents) == 8
        assert {d["lane"] for d in documents} == {"green"}
        assert {d["channel"] for d in documents} == {"migration"}

        users = {
            u["username"]: u
            for u in (await api.client.get(f"{PREFIX}/users", headers=helpers.auth(m.admin))).json()
        }
        assert {"tobias", "anna", "bob", "gone"} <= set(users)
        assert users["gone"]["active"] is False and users["anna"]["role"] == "user"
        by_title = {d["title"]: d for d in documents}
        assert by_title["Document 10"]["owner_id"] == users["tobias"]["id"]
        assert by_title["Document 14"]["owner_id"] == str(m.admin.id)  # no owner in Paperless
        assert by_title["Document 15"]["owner_id"] == str(m.admin.id)  # an owner who is gone
        assert by_title["Document 10"]["document_date"] == "2024-05-01"
        assert len(by_title["Document 10"]["fields"]) == 12

        drawers = (await api.client.get(f"{PREFIX}/drawers", headers=helpers.auth(m.admin))).json()
        shared = next(d for d in drawers if d["name"].startswith("Geteilt"))
        assert shared["owner_id"] == users["anna"]["id"]
        assert {(s["user_id"], s["level"]) for s in shared["shares"]} == {
            (users["bob"]["id"], "read"),
            (users["tobias"]["id"], "read_write"),
        }
        assert by_title["Document 13"]["drawer_id"] == shared["id"]
        # The owner sees it; bob reads it once it is green; a stranger does not.
        bob = await api.user("bob-session")
        assert (
            await api.client.get(
                f"{PREFIX}/documents/{by_title['Document 13']['id']}", headers=helpers.auth(bob)
            )
        ).status_code == 404

        log = (
            await api.client.get(
                f"{PREFIX}/documents/{by_title['Document 10']['id']}/log",
                headers=helpers.auth(m.admin),
            )
        ).json()
        imported = {e["step"]: e for e in log if e["model_version"] == "imported"}
        assert set(imported) == {"classify", "extract_fields"}

        assert await m.run("verify") == 0, m.messages
        assert await m.run("verify", rehash=True) == 0

        # A second run adds nothing.
        assert await m.run("run") == 0
        assert len(await m.documents()) == 8
        assert len(m.paperless.downloads) == 8 + 8  # the check downloaded the originals again


async def test_a_stopped_run_continues_against_the_api(api: Api, tmp_path: Path) -> None:
    async with migrating(api, tmp_path) as m:
        stop = asyncio.Event()

        async def watch() -> None:
            while len(await m.documents()) < 3:
                await asyncio.sleep(0.005)
            stop.set()

        watcher = asyncio.create_task(watch())
        assert await m.run("run", stop=stop, concurrency=1) == 130
        await watcher
        partial = len(await m.documents())
        assert 3 <= partial < 8

        assert await m.run("run") == 0
        assert len(await m.documents()) == 8
        assert await m.run("verify") == 0


async def test_a_second_state_finds_the_duplicates(api: Api, tmp_path: Path) -> None:
    async with migrating(api, tmp_path) as m:
        assert await m.run("run") == 0
        assert await m.run("run", state=tmp_path / "second.sqlite") == 0
        assert len(await m.documents()) == 8
        assert await m.run("verify", state=tmp_path / "second.sqlite") == 0
