"""Milestone M7 end to end: a user rule made over the REST API files arriving documents into
another drawer of the owner and tags them; the processing log names the rule and its version.
Real API server (Uvicorn) and worker; OCR and parser are fakes that bring the text of the test.
Two set-ups: SQLite with the filesystem, Postgres with S3 (Garage)."""

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx2
import pytest

from papiq.adapters.inbound.rest import PREFIX
from papiq.adapters.inbound.worker import Worker
from papiq.adapters.outbound.sql import Database, SqlEventBus, SqlUnitOfWorkFactory
from papiq.composition.api import build_app
from papiq.composition.container import (
    Container,
    Services,
    build_memory_container,
    build_services,
)
from papiq.composition.settings import Settings
from papiq.core.domain.users import Role, User
from papiq.core.ports import ObjectStore, OcrResult, ParseResult
from tests import builders
from tests.api import auth, issue_token, serving

DOCUMENTS = f"{PREFIX}/documents"
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

    async def user(self, role: Role = Role.USER) -> User:
        user = builders.user(role=role)
        async with self.container.unit_of_work() as uow:
            await uow.users.add(user)
            await uow.drawers.add(builders.default_drawer(user))
            await uow.commit()
        await issue_token(self.services.auth, user)
        return user

    async def post(self, user: User, path: str, body: dict[str, Any]) -> dict[str, Any]:
        response = await self.client.post(f"{PREFIX}{path}", json=body, headers=auth(user))
        assert response.status_code == 201, response.text
        created: dict[str, Any] = response.json()
        return created

    async def upload(self, user: User, text: str) -> str:
        files = {"file": ("bill.pdf", pdf(text), "application/pdf")}
        response = await self.client.post(DOCUMENTS, files=files, headers=auth(user))
        assert response.status_code == 202, response.text
        return str(response.json()["id"])

    async def settled(self, user: User, id: str) -> dict[str, Any]:
        """The document once processing has ended."""
        async with asyncio.timeout(TIMEOUT):
            while True:
                response = await self.client.get(f"{DOCUMENTS}/{id}", headers=auth(user))
                assert response.status_code == 200, response.text
                document: dict[str, Any] = response.json()
                if document["lane"] is not None:
                    return document
                await asyncio.sleep(0.2)

    async def rules_entry(self, user: User, id: str) -> dict[str, Any]:
        """The log entry of the step `apply_rules`."""
        response = await self.client.get(f"{DOCUMENTS}/{id}/log", headers=auth(user))
        assert response.status_code == 200, response.text
        (entry,) = [item for item in response.json() if item["step"] == "apply_rules"]
        found: dict[str, Any] = entry
        return found


@pytest.fixture
async def system(stores: tuple[Database, ObjectStore]) -> AsyncIterator[System]:
    database, object_store = stores
    container = replace(
        build_memory_container(),
        unit_of_work=SqlUnitOfWorkFactory(database),
        event_bus=SqlEventBus(database),
        object_store=object_store,
        ocr=TextOcr(),
        parser=TextParser(),
    )
    tuning = Settings.model_construct(
        worker_concurrency=2, events_poll_interval=timedelta(milliseconds=50)
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
            yield System(container, services, client)
    finally:
        worker.stop()
        await asyncio.wait_for(running, timeout=60)


async def test_a_user_rule_files_and_tags_arriving_documents(system: System) -> None:
    admin, owner = await system.user(Role.ADMIN), await system.user()
    energy = await system.post(admin, "/tags", {"name": "Energie"})
    drawer = await system.post(owner, "/drawers", {"name": "Strom"})
    body: dict[str, Any] = {
        "name": "Stromrechnungen",
        "triggers": ["ingest"],
        "conditions": {
            "all": [
                {"field": "text", "op": "matches", "value": r"Strom\s*rechnung"},
                {"any": [{"field": "text", "op": "contains", "value": "Mahnung"}], "not": True},
            ]
        },
        "actions": [
            {"type": "set_drawer", "drawer_id": drawer["id"]},
            {"type": "add_tags", "tag_ids": [energy["id"]]},
        ],
    }
    rule = await system.post(owner, "/rules", body)
    assert (rule["version"], rule["scope"], rule["owner_id"]) == (1, "user", str(owner.id))

    bill = await system.upload(owner, "Stromrechnung der Stadtwerke, Nr. 4711")
    reminder = await system.upload(owner, "Mahnung zur Stromrechnung Nr. 4711")

    # The bill is filed into the rule's drawer and tagged, without a review.
    filed = await system.settled(owner, bill)
    assert filed["lane"] == "green", filed
    assert filed["processing"]["status"] == "completed"
    assert filed["processing"]["current_step"] is None
    assert filed["processing"]["outcomes"]["apply_rules"] == "ok"
    assert filed["processing"]["outcomes"]["file"] == "ok"
    assert filed["drawer_id"] == drawer["id"]
    assert filed["tag_ids"] == [energy["id"]]
    entry = await system.rules_entry(owner, bill)
    assert entry["outcome"] == "ok", entry
    assert entry["model_version"] == "rules"
    assert entry["input"]["trigger"] == "ingest"
    assert {"id": rule["id"], "version": 1} in entry["input"]["rules"]
    (report,) = entry["output"]["rules"]
    assert (report["rule_id"], report["version"], report["scope"]) == (rule["id"], 1, "user")
    assert report["name"] == "Stromrechnungen"
    applied = {(effect["field"], effect["new"]) for effect in report["applied"]}
    assert applied == {("drawer", drawer["id"]), ("tags", energy["id"])}

    # The negated group keeps the reminder where it arrived.
    kept = await system.settled(owner, reminder)
    assert kept["lane"] == "green", kept
    assert kept["drawer_id"] != drawer["id"]
    assert kept["tag_ids"] == []
    entry = await system.rules_entry(owner, reminder)
    assert {"id": rule["id"], "version": 1} in entry["input"]["rules"]
    assert entry["output"].get("rules", []) == []

    # A changed rule acts with its new version, and the log says so.
    body["conditions"]["all"][0]["value"] = "Gasrechnung"
    changed = await system.client.put(
        f"{PREFIX}/rules/{rule['id']}", json=body, headers=auth(owner)
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["version"] == 2
    gas = await system.upload(owner, "Gasrechnung der Stadtwerke, Nr. 4712")
    filed = await system.settled(owner, gas)
    assert (filed["lane"], filed["drawer_id"]) == ("green", drawer["id"]), filed
    entry = await system.rules_entry(owner, gas)
    assert {"id": rule["id"], "version": 2} in entry["input"]["rules"]
    assert [(item["rule_id"], item["version"]) for item in entry["output"]["rules"]] == [
        (rule["id"], 2)
    ]
