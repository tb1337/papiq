"""Start the API: build the container and services, serve with Uvicorn until SIGTERM or SIGINT."""

import asyncio
import contextlib
import signal
import types
from collections.abc import Iterator
from uuid import UUID

import uvicorn
from fastapi import FastAPI

from papiq.adapters.inbound.rest import ApiContext, HealthCheck, close_event_streams, create_app
from papiq.adapters.outbound.filesystem import FilesystemObjectStore
from papiq.adapters.outbound.s3 import S3ObjectStore
from papiq.composition.bootstrap import ensure_first_admin
from papiq.composition.container import Container, Services, build_container, build_services
from papiq.composition.settings import Settings
from papiq.core.domain.ids import UserId

_SIGNALS = (signal.SIGTERM, signal.SIGINT)

# Seconds Uvicorn waits for open requests when stopping; event streams end at once.
SHUTDOWN_TIMEOUT = 10


def health_checks(container: Container) -> dict[str, HealthCheck]:
    """Reachability of the database, the object store and, if configured, the search index (the
    API works without it)."""

    async def database() -> None:
        async with container.unit_of_work() as uow:
            await uow.users.find(UserId(UUID(int=0)))

    store = container.object_store

    async def object_store() -> None:
        if isinstance(store, FilesystemObjectStore | S3ObjectStore):
            await store.check()  # the root directory or the bucket
        else:
            await store.exists("health/probe")

    checks: dict[str, HealthCheck] = {"database": database, "object_store": object_store}
    if container.search_index is not None:
        checks["search"] = container.search_index.check
    return checks


def build_app(
    container: Container, settings: Settings, services: Services | None = None
) -> FastAPI:
    services = services or build_services(container, settings)
    return create_app(
        ApiContext(
            auth=services.auth,
            users=services.users,
            drawers=services.drawers,
            master_data=services.master_data,
            oidc=services.oidc,
            search=services.search,
            indexing=services.indexing,
            cookie_secure=settings.cookie_secure,
            pipeline=services.pipeline,
            documents=services.documents,
            rules=services.rules,
            rule_applications=services.rule_applications,
            webhooks=services.webhooks,
            webhook_delivery=services.webhook_delivery,
            event_bus=container.event_bus,
            health_checks=health_checks(container),
            optional_checks=frozenset({"search"}),
            max_upload_size=int(settings.upload_max_size),
            max_request_size=int(settings.request_max_size),
            events_poll_interval=settings.events_poll_interval,
            mcp_enabled=settings.mcp_enabled,
            mcp_text_max=settings.mcp_text_max,
        )
    )


class _Server(uvicorn.Server):
    """Uvicorn with two changes on SIGTERM and SIGINT:

    - The event streams end at once; otherwise Uvicorn would wait for them until its shutdown
      timeout.
    - The signal is handled in the event loop and not raised again after shutdown (Uvicorn's
      default), so the caller can still close the database engine and exits normally.
    """

    def __init__(self, config: uvicorn.Config, app: FastAPI) -> None:
        super().__init__(config)
        self._app = app

    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        loop = asyncio.get_running_loop()
        for signum in _SIGNALS:
            loop.add_signal_handler(signum, self.handle_exit, signum, None)
        try:
            yield
        finally:
            for signum in _SIGNALS:
                loop.remove_signal_handler(signum)

    def handle_exit(self, sig: int, frame: types.FrameType | None) -> None:
        close_event_streams(self._app)
        super().handle_exit(sig, frame)


async def run_api(settings: Settings) -> None:
    """Serve the API until SIGTERM or SIGINT, then close the database engine and the object
    store."""
    container = build_container(settings)
    try:
        services = build_services(container, settings)
        await ensure_first_admin(services.users, settings)
        app = build_app(container, settings, services)
        config = uvicorn.Config(
            app,
            host=settings.api_host,
            port=settings.api_port,
            log_config=None,  # logging is configured by the composition root
            timeout_graceful_shutdown=SHUTDOWN_TIMEOUT,
            # The client address (failed sign-ins per source) comes from X-Forwarded-For only
            # behind the configured proxies.
            proxy_headers=settings.forwarded_allow_ips is not None,
            forwarded_allow_ips=settings.forwarded_allow_ips,
        )
        await _Server(config, app).serve()
    finally:
        await container.aclose()
