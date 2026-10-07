"""Server-sent events through a real Uvicorn server: each user receives only events of
documents they may read."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI

from papiq.adapters.inbound.rest import PREFIX, close_event_streams
from papiq.core.domain.drawers import ShareLevel
from papiq.core.domain.users import User
from tests.contracts.processing import SAMPLES
from tests.unit.adapters.rest.conftest import Api, auth

EVENTS = f"{PREFIX}/events"


@asynccontextmanager
async def serving(app: FastAPI) -> AsyncIterator[str]:
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_config=None))
    task = asyncio.create_task(server.serve())
    async with asyncio.timeout(5):
        while not server.started:
            await asyncio.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        close_event_streams(app)
        server.should_exit = True
        await asyncio.wait_for(task, timeout=10)


@dataclass
class Stream:
    """Collects the events of one open `GET /events`."""

    events: list[dict[str, Any]] = field(default_factory=list)
    status: int | None = None
    ready: asyncio.Event = field(default_factory=asyncio.Event)

    def types(self) -> list[str]:
        return [event["type"] for event in self.events]


async def listen(client: httpx.AsyncClient, user: User, stream: Stream, **params: str) -> None:
    async with client.stream("GET", EVENTS, headers=auth(user), params=params) as response:
        stream.status = response.status_code
        stream.ready.set()
        name = None
        async for line in response.aiter_lines():
            if line.startswith("event: "):
                name = line.removeprefix("event: ")
            elif line.startswith("data: "):
                data = json.loads(line.removeprefix("data: "))
                assert data["type"] == name
                stream.events.append(data)


async def until(condition: Any, timeout: float = 5) -> None:
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.01)


async def test_events_reach_only_users_who_may_read_the_document(api: Api) -> None:
    owner, reader, stranger = await api.user(), await api.user(), await api.user()
    shared = await api.services.drawers.create(owner.id, "Shared")
    await api.services.drawers.share(owner.id, shared.id, reader.id, ShareLevel.READ)

    async with serving(api.app) as url, httpx.AsyncClient(base_url=url, timeout=10) as client:
        streams = {user.id: Stream() for user in (owner, reader, stranger)}
        tasks = [
            asyncio.create_task(listen(client, user, streams[user.id]))
            for user in (owner, reader, stranger)
        ]
        for stream in streams.values():
            await asyncio.wait_for(stream.ready.wait(), timeout=5)
            assert stream.status == 200
        await asyncio.sleep(0.05)  # streams are registered once the first byte is out

        files = {"file": ("scan.pdf", (SAMPLES / "scan.pdf").read_bytes(), "x/y")}
        response = await client.post(
            f"{PREFIX}/documents",
            files=files,
            data={"drawer_id": str(shared.id)},
            headers=auth(owner),
        )
        document = response.json()["id"]
        own = streams[owner.id]
        await until(lambda: "document.received" in own.types())
        await api.drain()  # processing runs to green
        await until(lambda: own.events and own.events[-1]["type"] == "document.lane_changed")

        assert own.types()[:2] == ["document.received", "document.step_completed"]
        assert own.events[-1]["new"] == "green"
        assert {event["document_id"] for event in own.events} == {document}
        step = own.events[1]
        assert (step["step"], step["run"], step["outcome"]) == ("receive", 1, "ok")

        shared_events = streams[reader.id].types()
        assert "document.received" not in shared_events  # not visible while processing
        assert shared_events[-1] == "document.lane_changed"  # visible once green
        assert streams[stranger.id].events == []

        close_event_streams(api.app)
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=5)


async def test_a_stream_for_one_document(api: Api) -> None:
    owner, stranger = await api.user(), await api.user()
    async with serving(api.app) as url, httpx.AsyncClient(base_url=url, timeout=10) as client:
        files = {"file": ("scan.pdf", (SAMPLES / "scan.pdf").read_bytes(), "x/y")}
        first = (await client.post(f"{PREFIX}/documents", files=files, headers=auth(owner))).json()
        other = {"file": ("photo.jpg", (SAMPLES / "photo.jpg").read_bytes(), "x/y")}
        await asyncio.sleep(0.1)  # the received events of both are delivered before listening

        hidden = await client.get(
            EVENTS, params={"document_id": first["id"]}, headers=auth(stranger)
        )
        assert hidden.status_code == 404

        stream = Stream()
        task = asyncio.create_task(listen(client, owner, stream, document_id=first["id"]))
        await asyncio.wait_for(stream.ready.wait(), timeout=5)
        await asyncio.sleep(0.05)
        await client.post(f"{PREFIX}/documents", files=other, headers=auth(owner))
        await api.drain()
        await until(lambda: stream.types().count("document.lane_changed") == 1)
        assert {event["document_id"] for event in stream.events} == {first["id"]}
        close_event_streams(api.app)
        await asyncio.wait_for(task, timeout=5)
