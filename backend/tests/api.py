"""Helpers for tests of the REST API: real API tokens for test users, a real Uvicorn server,
and a reader for server-sent events."""

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import httpx2
import uvicorn
from fastapi import FastAPI

from papiq.adapters.inbound.rest import PREFIX, close_event_streams
from papiq.core.domain.identity import TokenScope
from papiq.core.domain.ids import UserId
from papiq.core.domain.users import User
from papiq.core.services.auth import AuthService

# API tokens of the test users, issued through the real AuthService.
_TOKENS: dict[UserId, str] = {}


async def issue_token(auth_service: AuthService, user: User) -> str:
    """A `read_write` token for `user`, remembered for `auth(user)`."""
    _, token = await auth_service.create_api_token(user.id, "tests", TokenScope.READ_WRITE)
    _TOKENS[user.id] = token
    return token


def auth(user: User) -> dict[str, str]:
    """Headers that authenticate `user` with their test token."""
    return bearer(_TOKENS[user.id])


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@asynccontextmanager
async def serving(app: FastAPI) -> AsyncIterator[str]:
    """Run `app` with Uvicorn on a free local port; yields the base URL."""
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_config=None))
    task = asyncio.create_task(server.serve())
    async with asyncio.timeout(10):
        while not server.started:
            await asyncio.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        close_event_streams(app)
        server.should_exit = True
        await asyncio.wait_for(task, timeout=15)


@dataclass
class Stream:
    """The events of one open `GET /events`."""

    events: list[dict[str, Any]] = field(default_factory=list)
    status: int | None = None
    ready: asyncio.Event = field(default_factory=asyncio.Event)

    def types(self) -> list[str]:
        return [event["type"] for event in self.events]

    def documents(self) -> set[str]:
        return {event["document_id"] for event in self.events}


async def listen(client: httpx2.AsyncClient, user: User, stream: Stream, **params: str) -> None:
    """Collect events into `stream` until the server ends the stream or goes away."""
    with contextlib.suppress(httpx2.ReadError, httpx2.RemoteProtocolError):
        await _listen(client, user, stream, params)


async def _listen(
    client: httpx2.AsyncClient, user: User, stream: Stream, params: dict[str, str]
) -> None:
    async with client.stream(
        "GET", f"{PREFIX}/events", headers=auth(user), params=params
    ) as response:
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


async def until(condition: Callable[[], object], timeout: float = 5) -> None:
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.02)
