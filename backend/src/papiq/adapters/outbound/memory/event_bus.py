"""In-memory event bus: delivers committed outbox events of a MemoryDatabase.

Events are appended to the outbox at commit, so outbox positions follow commit order and no
event can be skipped. Delivery state lives in the database; a new bus on the same database
resumes existing subscriptions. Purged events keep their positions; they are only marked.
"""

import asyncio
import logging
from datetime import UTC, datetime

from papiq.adapters.outbound.memory.database import MemoryDatabase, Retry, SubscriptionState
from papiq.core.ports.clock import Clock
from papiq.core.ports.event_bus import DEFAULT_DELIVERY_RETRY, DeliveryRetry, EventHandler

log = logging.getLogger(__name__)


class MemoryEventBus:
    def __init__(
        self,
        database: MemoryDatabase,
        *,
        clock: Clock | None = None,
        retry: DeliveryRetry = DEFAULT_DELIVERY_RETRY,
    ) -> None:
        self._db = database
        self._clock = clock
        self._retry = retry
        self._handlers: dict[str, EventHandler] = {}
        self._lock = asyncio.Lock()

    def subscribe(self, subscriber: str, handler: EventHandler) -> None:
        if subscriber in self._handlers:
            raise ValueError(f"subscriber {subscriber!r} is already registered")
        self._handlers[subscriber] = handler
        self._db.subscriptions.setdefault(subscriber, SubscriptionState(len(self._db.outbox)))

    async def dispatch(self, *, limit: int = 100) -> int:
        async with self._lock:
            delivered = 0
            for name, handler in list(self._handlers.items()):
                state = self._db.subscriptions[name]
                delivered += await self._deliver(name, handler, state, limit)
            return delivered

    async def purge(self, *, before: datetime) -> int:
        async with self._lock:
            states = self._db.subscriptions.values()
            end = min((state.cursor for state in states), default=len(self._db.outbox))
            waiting = {retry.position for state in states for retry in state.retries}
            purged = 0
            for position in range(end):
                event = self._db.outbox[position]
                recorded = self._db.recorded_at.get(event.id)
                if (
                    position in self._db.purged
                    or position in waiting
                    or recorded is None
                    or recorded >= before
                ):
                    continue
                self._db.purged.add(position)
                purged += 1
            return purged

    async def _deliver(
        self, name: str, handler: EventHandler, state: SubscriptionState, limit: int
    ) -> int:
        now = self._now()
        due = sorted(
            (retry for retry in state.retries if retry.due <= now),
            key=lambda retry: (retry.attempts, retry.position),
        )[:limit]
        attempts = {retry.position: retry.attempts for retry in due}
        end = min(len(self._db.outbox), state.cursor + limit)
        fresh = list(range(state.cursor, end))
        state.cursor = end
        failed: list[Retry] = []
        delivered = 0
        for position in [*attempts, *fresh]:
            event = self._db.outbox[position]
            try:
                await handler(event)
            except Exception:
                tries = attempts.get(position, 0) + 1
                retry_at = self._retry.next_attempt(tries, self._now())
                if retry_at is None:
                    log.exception(
                        "event delivery given up",
                        extra={"subscriber": name, "event_id": str(event.id), "attempts": tries},
                    )
                    continue
                log.warning(
                    "event handler failed",
                    extra={"subscriber": name, "event_id": str(event.id), "attempts": tries},
                    exc_info=True,
                )
                failed.append(Retry(position, tries, retry_at))
            else:
                delivered += 1
        state.retries = [
            *(retry for retry in state.retries if retry.position not in attempts),
            *failed,
        ]
        return delivered

    def _now(self) -> datetime:
        return self._clock.now() if self._clock is not None else datetime.now(UTC)
