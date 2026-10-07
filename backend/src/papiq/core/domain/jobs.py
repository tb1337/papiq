"""Background jobs: retryable units of work, e.g. one pipeline step for one document."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from papiq.core.domain.ids import JobId
from papiq.core.domain.json_value import JsonObject


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"  # claimed by a worker until `locked_until`
    DONE = "done"
    FAILED = "failed"  # given up

    @property
    def is_active(self) -> bool:
        return self in {JobStatus.QUEUED, JobStatus.RUNNING}


@dataclass(frozen=True, kw_only=True)
class Job:
    """A snapshot of a job as stored by the job queue.

    `dedup_key`: while a job with this key is queued or running, enqueueing another one with the
    same key does nothing.
    """

    id: JobId
    kind: str
    payload: JsonObject
    dedup_key: str | None
    status: JobStatus
    attempts: int  # claims so far, including the current one
    run_at: datetime
    locked_until: datetime | None = None
    last_error: str | None = None
    releases: int = 0  # claims given back unfinished (`JobQueue.release`)

    @property
    def tries(self) -> int:
        """Claims that counted: the attempts without the released ones."""
        return self.attempts - self.releases
