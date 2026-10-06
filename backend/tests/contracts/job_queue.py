from datetime import timedelta

import pytest

from papiq.core.domain.errors import ConcurrencyError, NotFoundError
from papiq.core.domain.ids import JobId, new_id
from papiq.core.domain.jobs import Job, JobStatus
from papiq.core.ports import UnitOfWorkFactory
from tests.builders import NOW

LEASE = timedelta(minutes=5)


async def enqueue(
    uow_factory: UnitOfWorkFactory,
    kind: str = "test",
    *,
    run_at: timedelta = timedelta(0),
    dedup_key: str | None = None,
) -> JobId | None:
    async with uow_factory() as uow:
        job = await uow.jobs.enqueue(kind, {"n": 1}, run_at=NOW + run_at, dedup_key=dedup_key)
        await uow.commit()
    return job


async def claim(
    uow_factory: UnitOfWorkFactory,
    *,
    at: timedelta = timedelta(0),
    kinds: list[str] | None = None,
) -> Job | None:
    async with uow_factory() as uow:
        job = await uow.jobs.claim(now=NOW + at, lease=LEASE, kinds=kinds)
        await uow.commit()
    return job


async def get(uow_factory: UnitOfWorkFactory, job: JobId) -> Job:
    async with uow_factory() as uow:
        return await uow.jobs.get(job)


class JobQueueContract:
    async def test_enqueued_job_is_stored(self, uow_factory: UnitOfWorkFactory) -> None:
        async with uow_factory() as uow:
            id = await uow.jobs.enqueue(
                "pipeline.step",
                {"document_id": "x", "step": "ocr", "run": 1, "extra": [1.5, None, {"a": True}]},
                run_at=NOW,
                dedup_key="x:1:ocr",
            )
            assert id is not None
            await uow.commit()
        job = await get(uow_factory, id)
        assert job.kind == "pipeline.step"
        assert job.payload == {
            "document_id": "x",
            "step": "ocr",
            "run": 1,
            "extra": [1.5, None, {"a": True}],
        }
        assert job.dedup_key == "x:1:ocr"
        assert (job.status, job.attempts, job.run_at) == (JobStatus.QUEUED, 0, NOW)
        assert (job.locked_until, job.last_error) == (None, None)

    async def test_job_exists_only_after_commit(self, uow_factory: UnitOfWorkFactory) -> None:
        async with uow_factory() as uow:
            id = await uow.jobs.enqueue("test", {}, run_at=NOW)
            assert id is not None
        assert await claim(uow_factory) is None
        async with uow_factory() as uow:
            with pytest.raises(NotFoundError):
                await uow.jobs.get(id)

    async def test_missing_job(self, uow_factory: UnitOfWorkFactory) -> None:
        async with uow_factory() as uow:
            with pytest.raises(NotFoundError):
                await uow.jobs.get(JobId(new_id()))

    async def test_claim_takes_due_jobs_most_overdue_first(
        self, uow_factory: UnitOfWorkFactory
    ) -> None:
        later = await enqueue(uow_factory, run_at=timedelta(minutes=2))
        earlier = await enqueue(uow_factory, run_at=timedelta(minutes=1))
        future = await enqueue(uow_factory, run_at=timedelta(hours=1))
        first = await claim(uow_factory, at=timedelta(minutes=3))
        second = await claim(uow_factory, at=timedelta(minutes=3))
        assert first is not None and first.id == earlier
        assert second is not None and second.id == later
        assert await claim(uow_factory, at=timedelta(minutes=3)) is None
        assert future is not None

    async def test_claim_locks_and_counts_attempts(self, uow_factory: UnitOfWorkFactory) -> None:
        id = await enqueue(uow_factory)
        job = await claim(uow_factory)
        assert job is not None and job.id == id
        assert job.status is JobStatus.RUNNING
        assert job.attempts == 1
        assert job.locked_until == NOW + LEASE
        assert await get(uow_factory, id) == job
        assert await claim(uow_factory, at=LEASE - timedelta(seconds=1)) is None

    async def test_expired_lease_releases_the_job(self, uow_factory: UnitOfWorkFactory) -> None:
        id = await enqueue(uow_factory)
        await claim(uow_factory)
        again = await claim(uow_factory, at=LEASE)
        assert again is not None and again.id == id
        assert again.attempts == 2
        assert again.locked_until == NOW + 2 * LEASE

    async def test_claim_filters_by_kind(self, uow_factory: UnitOfWorkFactory) -> None:
        await enqueue(uow_factory, "index")
        step = await enqueue(uow_factory, "pipeline.step", run_at=timedelta(seconds=1))
        job = await claim(uow_factory, at=timedelta(seconds=1), kinds=["pipeline.step"])
        assert job is not None and job.id == step
        assert await claim(uow_factory, at=timedelta(seconds=1), kinds=["pipeline.step"]) is None

    async def test_claim_without_commit_is_undone(self, uow_factory: UnitOfWorkFactory) -> None:
        id = await enqueue(uow_factory)
        async with uow_factory() as uow:
            assert await uow.jobs.claim(now=NOW, lease=LEASE) is not None
        job = await claim(uow_factory)
        assert job is not None and job.id == id and job.attempts == 1

    async def test_completed_job_does_not_run_again(self, uow_factory: UnitOfWorkFactory) -> None:
        id = await enqueue(uow_factory)
        assert id is not None
        await claim(uow_factory)
        async with uow_factory() as uow:
            await uow.jobs.complete(id)
            await uow.commit()
        job = await get(uow_factory, id)
        assert (job.status, job.locked_until) == (JobStatus.DONE, None)
        assert await claim(uow_factory, at=2 * LEASE) is None

    async def test_rescheduled_job_runs_again_later(self, uow_factory: UnitOfWorkFactory) -> None:
        id = await enqueue(uow_factory)
        assert id is not None
        await claim(uow_factory)
        async with uow_factory() as uow:
            await uow.jobs.reschedule(id, run_at=NOW + timedelta(minutes=1), error="timeout")
            await uow.commit()
        job = await get(uow_factory, id)
        assert (job.status, job.last_error, job.locked_until) == (
            JobStatus.QUEUED,
            "timeout",
            None,
        )
        assert await claim(uow_factory, at=timedelta(seconds=59)) is None
        again = await claim(uow_factory, at=timedelta(minutes=1))
        assert again is not None and again.id == id and again.attempts == 2

    async def test_failed_job_is_given_up(self, uow_factory: UnitOfWorkFactory) -> None:
        id = await enqueue(uow_factory)
        assert id is not None
        await claim(uow_factory)
        async with uow_factory() as uow:
            await uow.jobs.fail(id, error="broken file")
            await uow.commit()
        job = await get(uow_factory, id)
        assert (job.status, job.last_error, job.locked_until) == (
            JobStatus.FAILED,
            "broken file",
            None,
        )
        assert await claim(uow_factory, at=2 * LEASE) is None

    async def test_dedup_key_while_active(self, uow_factory: UnitOfWorkFactory) -> None:
        first = await enqueue(uow_factory, dedup_key="doc:1:ocr")
        assert first is not None
        assert await enqueue(uow_factory, dedup_key="doc:1:ocr") is None
        assert await enqueue(uow_factory, dedup_key="doc:1:parse") is not None
        await claim(uow_factory)
        assert await enqueue(uow_factory, dedup_key="doc:1:ocr") is None
        async with uow_factory() as uow:
            await uow.jobs.complete(first)
            await uow.commit()
        assert await enqueue(uow_factory, dedup_key="doc:1:ocr") is not None

    async def test_dedup_key_within_one_unit(self, uow_factory: UnitOfWorkFactory) -> None:
        async with uow_factory() as uow:
            assert await uow.jobs.enqueue("test", {}, run_at=NOW, dedup_key="k") is not None
            assert await uow.jobs.enqueue("test", {}, run_at=NOW, dedup_key="k") is None
            await uow.commit()

    async def test_a_job_is_never_claimed_twice(self, uow_factory: UnitOfWorkFactory) -> None:
        id = await enqueue(uow_factory)
        async with uow_factory() as first, uow_factory() as second:
            mine = await first.jobs.claim(now=NOW, lease=LEASE)
            theirs = await second.jobs.claim(now=NOW, lease=LEASE)
            await first.commit()
            assert mine is not None and mine.id == id
            if theirs is not None and theirs.id == id:
                with pytest.raises(ConcurrencyError):
                    await second.commit()
