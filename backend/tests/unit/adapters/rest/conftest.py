"""The API on in-memory adapters. Tests name the caller in a header: the override of
`current_user` exists only here; the application itself has no way to set a user (M4)."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta

import httpx
import pytest
from fastapi import FastAPI

from papiq.adapters.inbound.rest import ApiContext, create_app
from papiq.adapters.outbound.memory import ManualClock
from papiq.composition.container import (
    Container,
    Services,
    build_memory_container,
    build_services,
)
from papiq.core.domain.users import User
from tests import builders
from tests.api import allow_test_users

MAX_UPLOAD = 1024 * 1024


def make_app(
    container: Container,
    services: Services,
    *,
    max_upload: int = MAX_UPLOAD,
    poll: timedelta = timedelta(milliseconds=20),
) -> FastAPI:
    async def ok() -> None:
        pass

    app = create_app(
        ApiContext(
            pipeline=services.pipeline,
            documents=services.documents,
            event_bus=container.event_bus,
            health_checks={"database": ok, "object_store": ok},
            max_upload_size=max_upload,
            events_poll_interval=poll,
        )
    )
    return allow_test_users(app)


@dataclass
class Api:
    container: Container
    services: Services
    app: FastAPI
    client: httpx.AsyncClient

    async def user(self, name: str | None = None) -> User:
        user = builders.user(name)
        async with self.container.unit_of_work() as uow:
            await uow.users.add(user)
            await uow.drawers.add(builders.default_drawer(user))
            await uow.commit()
        return user

    async def drain(self) -> None:
        while await self.services.pipeline.run_next_job():
            pass


@pytest.fixture
async def api() -> AsyncIterator[Api]:
    container = build_memory_container(ManualClock(builders.NOW))
    services = build_services(container)
    app = make_app(container, services)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://papiq") as client:
        yield Api(container, services, app, client)
