"""In-memory event bus: delivers committed outbox events of a MemoryDatabase.

Events are appended to the outbox at commit, so outbox positions follow commit order and no
event can be skipped. Delivery state lives in the database; a new bus on the same database
resumes existing subscriptions.
"""

import asyncio
import logging

from papiq.adapters.outbound.memory.database import MemoryDatabase, SubscriptionState
from papiq.core.ports.event_bus import EventHandler

log = logging.getLogger(__name__)


class MemoryEventBus:
    def __init__(self, database: MemoryDatabase) -> None:
        self._db = database
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

    async def _deliver(
        self, name: str, handler: EventHandler, state: SubscriptionState, limit: int
    ) -> int:
        retries, waiting = state.retries[:limit], state.retries[limit:]
        end = min(len(self._db.outbox), state.cursor + limit)
        fresh = list(range(state.cursor, end))
        state.cursor = end
        failed: list[int] = []
        delivered = 0
        for position in [*retries, *fresh]:
            event = self._db.outbox[position]
            try:
                await handler(event)
            except Exception:
                log.exception(
                    "event handler failed", extra={"subscriber": name, "event_id": str(event.id)}
                )
                failed.append(position)
            else:
                delivered += 1
        # Failed again: behind the waiting ones, so repeated failures do not starve others.
        state.retries = [*waiting, *failed]
        return delivered
