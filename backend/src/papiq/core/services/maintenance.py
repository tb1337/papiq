"""Housekeeping as a recurring job: remove finished jobs, delivered events, ended sessions and
stale counts of failed sign-ins.

The cleanup is a job in the job queue under a fixed dedup key, so with several workers only one
of them runs it at a time. After each run it schedules the next one, in the same transaction
that completes the current run.
"""

import logging
from datetime import timedelta

from papiq.core.domain.errors import ConcurrencyError
from papiq.core.domain.identity import ACCOUNT_THROTTLE, SOURCE_THROTTLE
from papiq.core.ports import Clock, EventBus, UnitOfWorkFactory

log = logging.getLogger(__name__)

CLEANUP_JOB = "maintenance.cleanup"

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
        self._lease = lease

    async def schedule(self) -> None:
        """Make sure a cleanup is queued; due at once unless one is queued already."""
        async with self._uow() as uow:
            await uow.jobs.enqueue(CLEANUP_JOB, {}, run_at=self._clock.now(), dedup_key=CLEANUP_JOB)
            await uow.commit()

    async def run_next_job(self) -> bool:
        """Run the cleanup if it is due. Returns False if it was not."""
        async with self._uow() as uow:
            job = await uow.jobs.claim(
                now=self._clock.now(), lease=self._lease, kinds=[CLEANUP_JOB]
            )
            await uow.commit()
        if job is None:
            return False

        now = self._clock.now()
        before = now - self._retention
        next_run = now + self._interval
        try:
            events = await self._bus.purge(before=before)
            async with self._uow() as uow:
                jobs = await uow.jobs.purge(before=before)
                sessions = await uow.sessions.purge(now=now, idle_before=now - self._session_idle)
                failures = await uow.login_failures.purge(before=now - _FAILURE_RETENTION)
                await uow.jobs.complete(job)
                await uow.jobs.enqueue(CLEANUP_JOB, {}, run_at=next_run, dedup_key=CLEANUP_JOB)
                await uow.commit()
        except ConcurrencyError:
            log.warning("lost the claim of the cleanup job", extra={"job_id": str(job.id)})
            return True
        except Exception as error:
            log.exception("cleanup failed")
            async with self._uow() as uow:
                await uow.jobs.reschedule(job, run_at=next_run, error=repr(error))
                await uow.commit()
            return True
        log.info(
            "cleanup done",
            extra={
                "jobs_removed": jobs,
                "events_removed": events,
                "sessions_removed": sessions,
                "login_failures_removed": failures,
            },
        )
        return True
