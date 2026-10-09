"""Housekeeping as jobs.

- The recurring cleanup removes finished jobs, delivered events, ended sessions, stale counts
  of failed sign-ins, old webhook deliveries and the previous secrets of webhooks whose grace
  period is over.
- `documents.remove_files`, queued with the deletion of a document, removes its derivatives
  and its original, unless another document (of any owner) has the same file. Deleting the
  original and storing it for a new upload lock the original's key (`UnitOfWork.lock`), so an
  upload of the same file at that moment never ends up without its original.

The cleanup is a job in the job queue under a fixed dedup key, so with several workers only one
of them runs it at a time. After each run it schedules the next one, in the same transaction
that completes the current run.
"""

import logging
from datetime import datetime, timedelta
from uuid import UUID

from papiq.core.domain.documents import Sha256
from papiq.core.domain.errors import ConcurrencyError, ValidationError
from papiq.core.domain.identity import ACCOUNT_THROTTLE, SOURCE_THROTTLE
from papiq.core.domain.ids import DocumentId
from papiq.core.domain.jobs import Job
from papiq.core.ports import Clock, EventBus, ObjectStore, UnitOfWork, UnitOfWorkFactory
from papiq.core.services.objects import derivative_keys, original_key

log = logging.getLogger(__name__)

CLEANUP_JOB = "maintenance.cleanup"
REMOVE_FILES_JOB = "documents.remove_files"
"""Payload: `document_id`, `sha256` of the deleted document."""

# Attempts to remove the files of a deleted document, waiting a minute, doubling.
_REMOVE_ATTEMPTS = 5
_REMOVE_DELAY = timedelta(minutes=1)

# Failed sign-ins older than this count no more (see the throttle windows).
_FAILURE_RETENTION = max(ACCOUNT_THROTTLE.window, SOURCE_THROTTLE.window)


class MaintenanceService:
    def __init__(
        self,
        uow: UnitOfWorkFactory,
        clock: Clock,
        event_bus: EventBus,
        *,
        interval: timedelta,
        retention: timedelta,
        session_idle: timedelta = timedelta(days=1),
        lease: timedelta = timedelta(minutes=5),
        object_store: ObjectStore | None = None,
    ) -> None:
        """Every `interval`, remove finished jobs and delivered events older than
        `retention`, sessions that expired or were idle for `session_idle`, and counts of
        failed sign-ins that no longer matter."""
        self._uow = uow
        self._clock = clock
        self._bus = event_bus
        self._interval = interval
        self._retention = retention
        self._session_idle = session_idle
        self._store = object_store
        self._lease = lease

    async def schedule(self) -> None:
        """Make sure a cleanup is queued; due at once unless one is queued already."""
        async with self._uow() as uow:
            await uow.jobs.enqueue(CLEANUP_JOB, {}, run_at=self._clock.now(), dedup_key=CLEANUP_JOB)
            await uow.commit()

    async def run_next_job(self) -> bool:
        """Run a due housekeeping job. Returns False if none was due."""
        async with self._uow() as uow:
            job = await uow.jobs.claim(
                now=self._clock.now(), lease=self._lease, kinds=[CLEANUP_JOB, REMOVE_FILES_JOB]
            )
            await uow.commit()
        if job is None:
            return False
        if job.kind == REMOVE_FILES_JOB:
            await self._remove_files(job)
            return True
        await self._cleanup(job)
        return True

    async def _remove_files(self, job: Job) -> None:
        try:
            document = DocumentId(UUID(str(job.payload["document_id"])))
            sha256 = Sha256(str(job.payload["sha256"]))
        except (KeyError, ValueError, ValidationError) as error:
            async with self._uow() as uow:
                await uow.jobs.fail(job, error=f"invalid payload: {error}")
                await uow.commit()
            return
        assert self._store is not None, "removing files needs the object store"
        original = original_key(sha256)
        try:
            for key in derivative_keys(document):
                await self._store.delete(key)
            async with self._uow() as uow:
                await uow.lock(original)
                shared = await uow.documents.exists(sha256=sha256)
                if not shared:
                    await self._store.delete(original)
                await uow.jobs.complete(job)
                await uow.commit()
        except ConcurrencyError:
            log.warning("lost the claim of a job", extra={"job_id": str(job.id)})
            return
        except Exception as error:
            log.warning(
                "removing files failed", extra={"document_id": str(document)}, exc_info=True
            )
            async with self._uow() as uow:
                if job.tries >= _REMOVE_ATTEMPTS:
                    await uow.jobs.fail(job, error=repr(error))
                else:
                    delay = _REMOVE_DELAY * (1 << (job.tries - 1))
                    await uow.jobs.reschedule(
                        job, run_at=self._clock.now() + delay, error=repr(error)
                    )
                await uow.commit()
            return
        log.info(
            "files of a deleted document removed",
            extra={"document_id": str(document), "original_removed": not shared},
        )

    async def _cleanup(self, job: Job) -> None:
        now = self._clock.now()
        before = now - self._retention
        next_run = now + self._interval
        try:
            events = await self._bus.purge(before=before)
            async with self._uow() as uow:
                jobs = await uow.jobs.purge(before=before)
                sessions = await uow.sessions.purge(now=now, idle_before=now - self._session_idle)
                failures = await uow.login_failures.purge(before=now - _FAILURE_RETENTION)
                deliveries = await uow.webhooks.purge_deliveries(before=before)
                secrets = await _drop_expired_secrets(uow, now)
                await uow.jobs.complete(job)
                await uow.jobs.enqueue(CLEANUP_JOB, {}, run_at=next_run, dedup_key=CLEANUP_JOB)
                await uow.commit()
        except ConcurrencyError:
            log.warning("lost the claim of the cleanup job", extra={"job_id": str(job.id)})
            return
        except Exception as error:
            log.exception("cleanup failed")
            async with self._uow() as uow:
                await uow.jobs.reschedule(job, run_at=next_run, error=repr(error))
                await uow.commit()
            return
        log.info(
            "cleanup done",
            extra={
                "jobs_removed": jobs,
                "events_removed": events,
                "sessions_removed": sessions,
                "login_failures_removed": failures,
                "webhook_deliveries_removed": deliveries,
                "webhook_secrets_dropped": secrets,
            },
        )


async def _drop_expired_secrets(uow: UnitOfWork, now: datetime) -> int:
    """A renewed webhook secret's predecessor signs until the grace period ends; after that it
    is removed from the database (webhooks are few, so they are simply read)."""
    dropped = 0
    for webhook in await uow.webhooks.list_all():
        if webhook.drop_expired_secret(now):
            webhook.updated_at = now
            await uow.webhooks.update(webhook)
            dropped += 1
    return dropped
