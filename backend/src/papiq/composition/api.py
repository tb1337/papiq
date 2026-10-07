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
from papiq.composition.container import Container, build_container, build_services
from papiq.composition.settings import Settings
from papiq.core.domain.ids import UserId

_SIGNALS = (signal.SIGTERM, signal.SIGINT)

# Seconds Uvicorn waits for open requests when stopping; event streams end at once.
SHUTDOWN_TIMEOUT = 10


def health_checks(container: Container) -> dict[str, HealthCheck]:
    """Reachability of the database and the object store."""

    async def database() -> None:
        async with container.unit_of_work() as uow:
            await uow.users.find(UserId(UUID(int=0)))

    async def object_store() -> None:
        await container.object_store.exists("health/probe")

    return {"database": database, "object_store": object_store}


def build_app(container: Container, settings: Settings) -> FastAPI:
    services = build_services(container, settings)
    return create_app(
        ApiContext(
            pipeline=services.pipeline,
            documents=services.documents,
            event_bus=container.event_bus,
            health_checks=health_checks(container),
            max_upload_size=int(settings.upload_max_size),
            events_poll_interval=settings.events_poll_interval,
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
        app = build_app(container, settings)
        config = uvicorn.Config(
            app,
            host=settings.api_host,
            port=settings.api_port,
            log_config=None,  # logging is configured by the composition root
            timeout_graceful_shutdown=SHUTDOWN_TIMEOUT,
            proxy_headers=False,
        )
        await _Server(config, app).serve()
    finally:
        await container.aclose()
