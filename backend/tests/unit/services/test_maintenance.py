"""The cleanup job removes old finished jobs, delivered events, ended sessions and stale counts
of failed sign-ins, and schedules itself."""

from datetime import datetime, timedelta

import pytest

from papiq.adapters.outbound.memory import MemoryEventBus
from papiq.core.domain.errors import AuthenticationError
from papiq.core.domain.jobs import JobStatus
from papiq.core.services.maintenance import CLEANUP_JOB, REMOVE_FILES_JOB, MaintenanceService
from papiq.core.services.objects import derivative_keys, original_key
from tests.builders import PASSWORD, incoming
from tests.unit.services.conftest import World

INTERVAL = timedelta(hours=1)
RETENTION = timedelta(days=7)


def maintenance(world: World, bus: MemoryEventBus | None = None) -> MaintenanceService:
    return MaintenanceService(
        world.uow,
        world.clock,
        bus or MemoryEventBus(world.database, clock=world.clock),
        interval=INTERVAL,
        retention=RETENTION,
        object_store=world.object_store,
    )


def cleanup_jobs(world: World) -> list[JobStatus]:
    return [job.status for job in world.database.jobs.values() if job.kind == CLEANUP_JOB]


async def test_schedule_queues_one_cleanup(world: World) -> None:
    service = maintenance(world)
    await service.schedule()
    await service.schedule()
    assert cleanup_jobs(world) == [JobStatus.QUEUED]


async def test_cleanup_runs_and_schedules_the_next_one(world: World) -> None:
    service = maintenance(world)
    owner = await world.user()
    await world.pipeline().receive(owner.id, incoming(b"%PDF-1"), filename="a.pdf")
    await world.drain()
    step_jobs = len(world.database.jobs)

    await service.schedule()
    world.clock.advance(RETENTION)  # the step jobs are not older than the retention yet
    assert await service.run_next_job()
    assert len(world.database.jobs) == step_jobs + 2
    assert sorted(cleanup_jobs(world)) == [JobStatus.DONE, JobStatus.QUEUED]
    assert not await service.run_next_job()  # the next one is due in an hour

    world.clock.advance(INTERVAL)
    assert await service.run_next_job()
    assert sorted(cleanup_jobs(world)) == [JobStatus.DONE, JobStatus.QUEUED]  # old run removed
    assert len(world.database.jobs) == 2


async def test_delivered_events_are_purged(world: World) -> None:
    bus = MemoryEventBus(world.database, clock=world.clock)
    received: list[object] = []

    async def handler(event: object) -> None:
        received.append(event)

    bus.subscribe("test", handler)
    owner = await world.user()
    await world.pipeline().receive(owner.id, incoming(b"%PDF-1"), filename="a.pdf")
    await bus.dispatch()
    service = maintenance(world, bus)
    await service.schedule()
    world.clock.set(max(world.database.recorded_at.values()) + RETENTION + timedelta(seconds=1))
    assert await service.run_next_job()
    assert len(world.database.purged) == len(received) == 2


async def test_a_failed_cleanup_is_tried_again_later(world: World) -> None:
    class BrokenBus(MemoryEventBus):
        async def purge(self, *, before: datetime) -> int:
            raise OSError("disk full")

    service = maintenance(world, BrokenBus(world.database))
    await service.schedule()
    assert await service.run_next_job()
    (job,) = world.database.jobs.values()
    assert (job.status, job.run_at, job.last_error) == (
        JobStatus.QUEUED,
        world.clock.now() + INTERVAL,
        "OSError('disk full')",
    )


async def test_cleanup_removes_ended_sessions_and_old_failures(world: World) -> None:
    service = maintenance(world)
    await world.account("alice")
    signed_in = await world.auth.login("alice", PASSWORD)
    with pytest.raises(AuthenticationError):
        await world.auth.login("alice", "wrong password!")
    await service.schedule()
    world.clock.advance(timedelta(days=1, minutes=1))  # session idle, failure past its window
    assert await service.run_next_job()
    async with world.uow() as uow:
        assert await uow.sessions.find_by_token(signed_in.session.token_hash) is None
        assert await uow.login_failures.find("account:alice") is None


async def test_deleting_a_document_removes_its_files(world: World) -> None:
    service = maintenance(world)
    alice, bob = await world.user(), await world.user()
    shared_file, own_file = b"%PDF-1.7 same", b"%PDF-1.7 alice only"
    kept = await world.pipeline().receive(alice.id, incoming(shared_file), filename="a.pdf")
    gone = await world.pipeline().receive(alice.id, incoming(own_file), filename="b.pdf")
    other = await world.pipeline().receive(bob.id, incoming(shared_file), filename="c.pdf")
    await world.drain()
    for document in (kept, gone):
        for key in derivative_keys(document.id):
            await world.object_store.put(key, b"x", content_type="x")

    await world.documents.delete(alice.id, gone.id)
    await world.documents.delete(alice.id, kept.id)
    assert await service.run_next_job() and await service.run_next_job()
    assert not await service.run_next_job()

    for document in (kept, gone):
        for key in derivative_keys(document.id):
            assert not await world.object_store.exists(key)
    assert not await world.object_store.exists(original_key(gone.sha256))
    assert await world.object_store.exists(original_key(other.sha256))  # Bob still has it
    assert [j.status for j in world.database.jobs.values() if j.kind == REMOVE_FILES_JOB] == [
        JobStatus.DONE,
        JobStatus.DONE,
    ]


async def test_removing_files_is_retried(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    service = maintenance(world)
    owner = await world.user()
    document = await world.pipeline().receive(owner.id, incoming(b"%PDF-1.7 x"), filename="x.pdf")
    await world.drain()
    await world.documents.delete(owner.id, document.id)

    async def broken(key: str) -> None:
        raise OSError("storage unreachable")

    monkeypatch.setattr(world.object_store, "delete", broken)
    assert await service.run_next_job()
    [job] = [j for j in world.database.jobs.values() if j.kind == REMOVE_FILES_JOB]
    assert job.status is JobStatus.QUEUED and job.last_error is not None
    monkeypatch.undo()
    world.clock.advance(timedelta(minutes=1))
    assert await service.run_next_job()
    assert not await world.object_store.exists(original_key(document.sha256))
