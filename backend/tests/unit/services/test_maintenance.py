"""The cleanup job removes old finished jobs and delivered events and schedules itself."""

from datetime import datetime, timedelta

from papiq.adapters.outbound.memory import MemoryEventBus
from papiq.core.domain.jobs import JobStatus
from papiq.core.services.maintenance import CLEANUP_JOB, MaintenanceService
from tests.builders import incoming
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
