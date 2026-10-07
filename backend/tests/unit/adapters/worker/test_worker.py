"""The worker service on in-memory adapters: runs jobs, delivers events, stops cleanly."""

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import timedelta

import pytest

from papiq.adapters.inbound.worker import Worker
from papiq.adapters.outbound.memory import MemoryEventBus
from papiq.composition.container import Container, Services, build_memory_container, build_services
from papiq.core.domain.documents import Document
from papiq.core.domain.events import DomainEvent
from papiq.core.domain.pipeline import Lane, Step, StepResult
from papiq.core.domain.users import User
from papiq.core.services.maintenance import CLEANUP_JOB
from tests import builders
from tests.contracts.processing import SAMPLES

FAST = timedelta(milliseconds=10)


def worker(
    container: Container, services: Services, *, shutdown_timeout: timedelta = timedelta(seconds=5)
) -> Worker:
    return Worker(
        pipeline=services.pipeline,
        maintenance=services.maintenance,
        event_bus=container.event_bus,
        concurrency=2,
        poll_interval=FAST,
        dispatch_interval=FAST,
        shutdown_timeout=shutdown_timeout,
    )


@asynccontextmanager
async def running(service: Worker) -> AsyncIterator[asyncio.Task[None]]:
    task = asyncio.create_task(service.run())
    try:
        yield task
    finally:
        service.stop()
        await asyncio.wait_for(task, timeout=10)


async def until(condition: Callable[[], object], timeout: float = 5) -> None:
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.01)


async def owner(container: Container) -> User:
    user = builders.user()
    async with container.unit_of_work() as uow:
        await uow.users.add(user)
        await uow.drawers.add(builders.default_drawer(user))
        await uow.commit()
    return user


async def lane(services: Services, user: User, document: Document) -> Lane | None:
    return (await services.documents.get(user.id, document.id)).lane


class Slow:
    def __init__(self, seconds: float | None) -> None:
        self.seconds = seconds
        self.started = asyncio.Event()

    async def run(self, document: Document) -> StepResult:
        self.started.set()
        if self.seconds is None:
            await asyncio.Event().wait()
        await asyncio.sleep(self.seconds or 0)
        return builders.OK


def with_ocr(services: Services, executor: Slow) -> Services:
    pipeline = services.pipeline
    pipeline._executors[Step.OCR] = executor
    return replace(services, pipeline=pipeline)


async def test_documents_are_processed_and_events_delivered() -> None:
    container = build_memory_container()
    services = build_services(container)
    received: list[DomainEvent] = []

    async def record(event: DomainEvent) -> None:
        received.append(event)

    container.event_bus.subscribe("test", record)
    user = await owner(container)
    async with running(worker(container, services)):
        document = await services.pipeline.receive(
            user.id, (SAMPLES / "scan.pdf").read_bytes(), filename="a.pdf", media_type="x/y"
        )
        await until(lambda: received and received[-1].type == "document.lane_changed")
    assert await lane(services, user, document) is Lane.GREEN
    assert received[0].type == "document.received"


async def test_the_cleanup_is_scheduled_at_start() -> None:
    container = build_memory_container()
    services = build_services(container)
    async with running(worker(container, services)):
        await asyncio.sleep(0.05)
    async with container.unit_of_work() as uow:
        job = await uow.jobs.claim(now=container.clock.now() + timedelta(days=1), lease=FAST)
    assert job is not None and job.kind == CLEANUP_JOB


async def test_stop_lets_running_jobs_finish() -> None:
    container = build_memory_container()
    step = Slow(0.2)
    services = with_ocr(build_services(container), step)
    user = await owner(container)
    document = await services.pipeline.receive(
        user.id, (SAMPLES / "scan.pdf").read_bytes(), filename="a.pdf", media_type="x/y"
    )
    async with running(worker(container, services)):
        await step.started.wait()
    log = await services.documents.processing_log(user.id, document.id)
    assert [entry.step for entry in log] == [Step.RECEIVE, Step.OCR]  # nothing new after stop


async def test_jobs_still_running_after_the_timeout_are_released() -> None:
    container = build_memory_container()
    step = Slow(None)
    services = with_ocr(build_services(container), step)
    user = await owner(container)
    await services.pipeline.receive(
        user.id, (SAMPLES / "scan.pdf").read_bytes(), filename="a.pdf", media_type="x/y"
    )
    async with running(worker(container, services, shutdown_timeout=FAST)):
        await step.started.wait()
    async with container.unit_of_work() as uow:
        job = await uow.jobs.claim(now=container.clock.now(), lease=FAST, kinds=["pipeline.step"])
    assert job is not None
    assert (job.attempts, job.last_error) == (2, "interrupted: the worker stopped")


async def test_a_failing_loop_keeps_running(monkeypatch: pytest.MonkeyPatch) -> None:
    container = build_memory_container()
    services = build_services(container)
    calls = 0
    original = MemoryEventBus.dispatch

    async def flaky(self: MemoryEventBus, *, limit: int = 100) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("database gone")
        return await original(self, limit=limit)

    monkeypatch.setattr(MemoryEventBus, "dispatch", flaky)
    async with running(worker(container, services)):
        await until(lambda: calls >= 2)


async def test_stop_before_start_returns_at_once() -> None:
    container = build_memory_container()
    service = worker(container, build_services(container))
    service.stop()
    await asyncio.wait_for(service.run(), timeout=2)
