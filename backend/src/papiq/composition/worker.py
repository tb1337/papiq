"""Start the worker service: build the container and services, run until SIGTERM or SIGINT."""

import asyncio
import signal

from papiq.adapters.inbound.worker import Worker
from papiq.composition.container import build_container, build_services
from papiq.composition.settings import Settings
from papiq.core.services.indexing import SUBSCRIBER


async def run_worker(settings: Settings) -> None:
    """Run the worker until it receives SIGTERM or SIGINT; then finish running jobs (up to
    `PAPIQ_WORKER_SHUTDOWN_TIMEOUT`) and close the database engine and the object store."""
    container = build_container(settings)
    try:
        services = build_services(container, settings)
        if services.indexing is not None:
            container.event_bus.subscribe(SUBSCRIBER, services.indexing.on_event)
        worker = Worker(
            pipeline=services.pipeline,
            maintenance=services.maintenance,
            event_bus=container.event_bus,
            concurrency=settings.worker_concurrency,
            poll_interval=settings.worker_poll_interval,
            dispatch_interval=settings.events_poll_interval,
            shutdown_timeout=settings.worker_shutdown_timeout,
            indexing=services.indexing,
        )
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(signum, worker.stop)
        try:
            await worker.run()
        finally:
            for signum in (signal.SIGTERM, signal.SIGINT):
                loop.remove_signal_handler(signum)
    finally:
        await container.aclose()
