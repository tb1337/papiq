"""The worker service: runs background jobs, delivers events and keeps the cleanup scheduled.

- `concurrency` job loops claim and run due jobs (the cleanup when due, then search indexing,
  else pipeline steps);
  a loop that finds nothing waits `poll_interval`.
- `webhook_concurrency` further loops deliver webhooks, apart from the job loops: a receiver that
  does not answer holds up deliveries, never the pipeline.
- One loop dispatches the outbox (`EventBus.dispatch`) every `dispatch_interval` while there is
  nothing to deliver, and at once again while there is.
- After an error, a loop waits longer, doubling up to `MAX_BACKOFF`.

`stop()` (on SIGTERM) ends the loops after their current job. Jobs still running after
`shutdown_timeout` are cancelled: the pipeline releases their jobs to run again.

Each loop runs its units of work one after another, never nested, as SQLite requires.
"""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable, Sequence
from datetime import timedelta

from papiq.core.ports import EventBus
from papiq.core.services.indexing import IndexingService
from papiq.core.services.maintenance import MaintenanceService
from papiq.core.services.pipeline import PipelineService
from papiq.core.services.rules.retroactive import RuleApplicationService
from papiq.core.services.webhooks import WebhookDeliveryService

log = logging.getLogger(__name__)

MAX_BACKOFF = timedelta(seconds=30)


class Worker:
    def __init__(
        self,
        *,
        pipeline: PipelineService,
        maintenance: MaintenanceService,
        event_bus: EventBus,
        concurrency: int,
        poll_interval: timedelta,
        dispatch_interval: timedelta,
        shutdown_timeout: timedelta,
        indexing: IndexingService | None = None,
        rules: RuleApplicationService | None = None,
        webhooks: WebhookDeliveryService | None = None,
        webhook_concurrency: int = 4,
    ) -> None:
        self._pipeline = pipeline
        self._rules = rules
        self._maintenance = maintenance
        self._indexing = indexing
        self._webhooks = webhooks
        self._webhook_concurrency = webhook_concurrency
        self._bus = event_bus
        self._concurrency = concurrency
        self._poll = poll_interval
        self._dispatch_interval = dispatch_interval
        self._shutdown_timeout = shutdown_timeout
        self._stopping = asyncio.Event()

    def stop(self) -> None:
        """Ask the worker to finish; `run` returns once it has."""
        if not self._stopping.is_set():
            log.info("worker stopping")
        self._stopping.set()

    async def run(self) -> None:
        """Run until `stop()`."""
        log.info("worker started", extra={"concurrency": self._concurrency})
        await self._maintenance.schedule()
        if self._indexing is not None:
            await self._indexing.schedule()
        loops = [
            *(
                asyncio.create_task(self._loop(f"jobs-{n}", self._run_job, self._poll))
                for n in range(1, self._concurrency + 1)
            ),
            *(
                asyncio.create_task(self._loop(f"webhooks-{n}", self._deliver, self._poll))
                for n in range(1, self._webhook_concurrency + 1)
                if self._webhooks is not None
            ),
            asyncio.create_task(self._loop("events", self._dispatch, self._dispatch_interval)),
        ]
        try:
            await self._stopping.wait()
        finally:
            await self._finish(loops)
        log.info("worker stopped")

    async def _finish(self, loops: Sequence[asyncio.Task[None]]) -> None:
        _, pending = await asyncio.wait(loops, timeout=self._shutdown_timeout.total_seconds())
        if pending:
            log.warning("cancelling running jobs", extra={"count": len(pending)})
            for task in pending:
                task.cancel()
            await asyncio.wait(pending)

    async def _loop(
        self, name: str, work: Callable[[], Awaitable[bool]], interval: timedelta
    ) -> None:
        """Call `work` until stopped; wait `interval` while it has nothing to do."""
        failures = 0
        while not self._stopping.is_set():
            try:
                busy = await work()
            except Exception:
                failures += 1
                log.exception("worker loop failed", extra={"loop": name, "failures": failures})
                await self._sleep(min(interval * (1 << min(failures, 10)), MAX_BACKOFF))
                continue
            failures = 0
            if not busy:
                await self._sleep(interval)

    async def _run_job(self) -> bool:
        # The cleanup first: it is due once per interval and would otherwise wait for a quiet
        # moment, which a long bulk ingest never has.
        return (
            await self._maintenance.run_next_job()
            or (self._indexing is not None and await self._indexing.run_next_job())
            or await self._pipeline.run_next_job()
            or (self._rules is not None and await self._rules.run_next_job())
        )

    async def _deliver(self) -> bool:
        return self._webhooks is not None and await self._webhooks.run_next_job()

    async def _dispatch(self) -> bool:
        return await self._bus.dispatch() > 0

    async def _sleep(self, duration: timedelta) -> None:
        """Wait `duration`, or less if the worker is stopped."""
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._stopping.wait(), timeout=duration.total_seconds())
