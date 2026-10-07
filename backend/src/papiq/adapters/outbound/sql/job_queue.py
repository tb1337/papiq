"""Job queue on the `jobs` table.

Claiming is one statement: `UPDATE jobs ... WHERE id = (SELECT the most overdue due job)`.
On Postgres the subquery locks the row with `FOR UPDATE SKIP LOCKED`, so concurrent workers
take different jobs without waiting. On SQLite the statement runs under the write lock taken by
`BEGIN IMMEDIATE`, so a second worker waits until the first commits and then sees the job taken.

Finishing a job checks the claim in the same statement (`status = 'running'` and the attempt
count of the claim): only the current claim can complete, reschedule or fail it.

Deduplication uses a unique index on `dedup_key` over queued and running jobs only;
`INSERT ... ON CONFLICT DO NOTHING` adds nothing while such a job exists. The index predicate
in ON CONFLICT is literal SQL (see `_ACTIVE`).
"""

from collections.abc import Collection, Mapping
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Row, and_, delete, insert, or_, select, text, update
from sqlalchemy.dialects import postgresql, sqlite

from papiq.adapters.outbound.sql import tables as t
from papiq.adapters.outbound.sql.transaction import Transaction
from papiq.core.domain.errors import ConcurrencyError, NotFoundError
from papiq.core.domain.ids import JobId, new_id
from papiq.core.domain.jobs import Job, JobStatus
from papiq.core.domain.json_value import JsonValue
from papiq.core.domain.validation import require_utc

_jobs = t.jobs

# The predicate of the partial dedup index, as literal SQL: Postgres matches the index of
# ON CONFLICT against it, which fails for bind parameters once a prepared statement is
# planned generically (from its sixth run on).
_ACTIVE = text("status IN (" + ", ".join(f"'{status}'" for status in t.ACTIVE_JOB_STATUSES) + ")")


class SqlJobQueue:
    def __init__(self, transaction: Transaction) -> None:
        self._tx = transaction

    async def enqueue(
        self,
        kind: str,
        payload: Mapping[str, JsonValue],
        *,
        run_at: datetime,
        dedup_key: str | None = None,
    ) -> JobId | None:
        id = JobId(new_id())
        values = {
            "id": id,
            "kind": kind,
            "payload": dict(payload),
            "dedup_key": dedup_key,
            "status": JobStatus.QUEUED.value,
            "attempts": 0,
            "run_at": require_utc(run_at, "run_at"),
        }
        if dedup_key is None:
            await self._tx.write(insert(_jobs).values(values))
            return id
        dialect = postgresql if not self._tx.database.is_sqlite else sqlite
        statement = (
            dialect.insert(_jobs)
            .values(values)
            .on_conflict_do_nothing(
                index_elements=[_jobs.c.dedup_key],
                index_where=_ACTIVE,
            )
            .returning(_jobs.c.id)
        )
        result = await self._tx.write(statement)
        return id if result.first() is not None else None

    async def claim(
        self, *, now: datetime, lease: timedelta, kinds: Collection[str] | None = None
    ) -> Job | None:
        due = or_(
            and_(_jobs.c.status == JobStatus.QUEUED.value, _jobs.c.run_at <= now),
            and_(_jobs.c.status == JobStatus.RUNNING.value, _jobs.c.locked_until <= now),
        )
        if kinds is not None:
            due = and_(due, _jobs.c.kind.in_(list(kinds)))
        candidate = (
            select(_jobs.c.id)
            .where(due)
            .order_by(_jobs.c.run_at, _jobs.c.id)
            .limit(1)
            .with_for_update(skip_locked=True)
            .scalar_subquery()
        )
        result = await self._tx.write(
            update(_jobs)
            .where(_jobs.c.id == candidate)
            .values(
                status=JobStatus.RUNNING.value,
                attempts=_jobs.c.attempts + 1,
                locked_until=require_utc(now, "now") + lease,
            )
            .returning(*_jobs.c)
        )
        row = result.first()
        return None if row is None else _job(row)

    async def complete(self, job: Job) -> None:
        await self._finish(job, status=JobStatus.DONE)

    async def reschedule(self, job: Job, *, run_at: datetime, error: str) -> None:
        await self._finish(
            job, status=JobStatus.QUEUED, run_at=require_utc(run_at, "run_at"), last_error=error
        )

    async def fail(self, job: Job, *, error: str) -> None:
        await self._finish(job, status=JobStatus.FAILED, last_error=error)

    async def get(self, job: JobId) -> Job:
        row = (await self._tx.read(select(_jobs).where(_jobs.c.id == job))).first()
        if row is None:
            raise NotFoundError("job", job)
        return _job(row)

    async def purge(self, *, before: datetime) -> int:
        result = await self._tx.write(
            delete(_jobs).where(
                _jobs.c.status.in_([JobStatus.DONE.value, JobStatus.FAILED.value]),
                _jobs.c.run_at < require_utc(before, "before"),
            )
        )
        return int(result.rowcount)

    async def _finish(self, job: Job, *, status: JobStatus, **values: Any) -> None:
        result = await self._tx.write(
            update(_jobs)
            .where(
                _jobs.c.id == job.id,
                _jobs.c.status == JobStatus.RUNNING.value,
                _jobs.c.attempts == job.attempts,
            )
            .values(status=status.value, locked_until=None, **values)
        )
        if result.rowcount == 0:
            await self.get(job.id)  # NotFoundError if it does not exist
            raise ConcurrencyError(f"job {job.id} is no longer held by this claim")


def _job(row: Row[Any]) -> Job:
    return Job(
        id=JobId(row.id),
        kind=row.kind,
        payload=row.payload,
        dedup_key=row.dedup_key,
        status=JobStatus(row.status),
        attempts=row.attempts,
        run_at=row.run_at,
        locked_until=row.locked_until,
        last_error=row.last_error,
    )
