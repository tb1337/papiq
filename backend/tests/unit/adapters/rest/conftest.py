"""The API on in-memory adapters. Callers authenticate for real: test users get an API token
(`tests.api.auth`) or sign in with their password (`Api.sign_in`)."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta

import httpx2
import pytest
from fastapi import FastAPI

from papiq.adapters.inbound.rest import PREFIX, ApiContext, create_app
from papiq.adapters.inbound.rest.auth import CSRF_HEADER
from papiq.adapters.outbound.memory import ManualClock
from papiq.composition.container import (
    Container,
    Services,
    build_memory_container,
    build_services,
)
from papiq.core.domain.identity import Credential, check_new_password
from papiq.core.domain.users import Role, User
from tests import builders
from tests.api import issue_token
from tests.builders import PASSWORD

MAX_UPLOAD = 1024 * 1024


def make_app(
    container: Container,
    services: Services,
    *,
    max_upload: int = MAX_UPLOAD,
    poll: timedelta = timedelta(milliseconds=20),
    cookie_secure: bool = True,
    recheck: timedelta = timedelta(seconds=30),
) -> FastAPI:
    async def ok() -> None:
        pass

    return create_app(
        ApiContext(
            auth=services.auth,
            users=services.users,
            drawers=services.drawers,
            master_data=services.master_data,
            oidc=services.oidc,
            search=services.search,
            indexing=services.indexing,
            pipeline=services.pipeline,
            documents=services.documents,
            rules=services.rules,
            rule_applications=services.rule_applications,
            event_bus=container.event_bus,
            health_checks={"database": ok, "object_store": ok},
            max_upload_size=max_upload,
            events_poll_interval=poll,
            cookie_secure=cookie_secure,
            stream_recheck_interval=recheck,
        )
    )


@dataclass
class Session:
    """A signed-in browser: its client holds the cookie; `headers` carry the CSRF token."""

    client: httpx2.AsyncClient
    csrf_token: str

    @property
    def headers(self) -> dict[str, str]:
        return {CSRF_HEADER: self.csrf_token}


@dataclass
class Api:
    container: Container
    services: Services
    app: FastAPI
    client: httpx2.AsyncClient
    clock: ManualClock

    async def user(self, name: str | None = None, role: Role = Role.USER) -> User:
        """A user with default drawer, password `PASSWORD` and an API token (`auth`)."""
        user = builders.user(name, role=role)
        async with self.container.unit_of_work() as uow:
            await uow.users.add(user)
            await uow.drawers.add(builders.default_drawer(user))
            await uow.credentials.add(
                Credential(
                    user_id=user.id,
                    password_hash=await self.container.password_hasher.hash(
                        check_new_password(PASSWORD, user.username)
                    ),
                )
            )
            await uow.commit()
        await issue_token(self.services.auth, user)
        return user

    async def admin(self, name: str | None = None) -> User:
        return await self.user(name, Role.ADMIN)

    def new_client(self) -> httpx2.AsyncClient:
        transport = httpx2.ASGITransport(app=self.app)
        return httpx2.AsyncClient(transport=transport, base_url="https://papiq")

    @asynccontextmanager
    async def sign_in(self, user: User, password: str = PASSWORD) -> AsyncIterator[Session]:
        """A new client (browser) signed in as `user`."""
        async with self.new_client() as client:
            response = await client.post(
                f"{PREFIX}/auth/login", json={"username": user.username, "password": password}
            )
            assert response.status_code == 200, response.text
            yield Session(client, response.json()["csrf_token"])

    async def drain(self) -> None:
        while await self.services.pipeline.run_next_job():
            pass


@pytest.fixture
async def api() -> AsyncIterator[Api]:
    clock = ManualClock(builders.NOW)
    container = build_memory_container(clock)
    services = build_services(container)
    builders.skip_classification(services.pipeline)
    app = make_app(container, services)
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="https://papiq") as client:
        yield Api(container, services, app, client, clock)
