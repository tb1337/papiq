from collections.abc import Collection, Mapping
from datetime import datetime, timedelta
from typing import Protocol

from papiq.core.domain.ids import JobId
from papiq.core.domain.jobs import Job
from papiq.core.domain.json_value import JsonValue


class JobQueue(Protocol):
    """Retryable background jobs. Part of the unit of work: a job is enqueued in the same
    transaction as the state change that causes it and only exists after commit.

    Claiming is a transaction of its own: claim, commit, then do the work, then complete or
    reschedule in a new unit of work. A claimed job whose lease runs out can be claimed again.

    First adapter: job table in the database (M2).
    """

    async def enqueue(
        self,
        kind: str,
        payload: Mapping[str, JsonValue],
        *,
        run_at: datetime,
        dedup_key: str | None = None,
    ) -> JobId | None:
        """Add a job, due at `run_at`. Returns None, and adds nothing, if a queued or running
        job with the same `dedup_key` exists."""
        ...

    async def claim(
        self, *, now: datetime, lease: timedelta, kinds: Collection[str] | None = None
    ) -> Job | None:
        """Lock the most overdue job for `lease`: a queued job with `run_at <= now`, or a running
        job whose lease has expired. Counts the attempt. None if no job is due."""
        ...

    async def complete(self, job: JobId) -> None: ...

    async def reschedule(self, job: JobId, *, run_at: datetime, error: str) -> None:
        """Release the job to run again at `run_at`, e.g. after a failed attempt."""
        ...

    async def fail(self, job: JobId, *, error: str) -> None:
        """Give the job up; it will not run again."""
        ...

    async def get(self, job: JobId) -> Job:
        """NotFoundError if the job does not exist."""
        ...
