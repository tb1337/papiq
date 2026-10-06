"""In-memory event bus: delivers committed outbox events of a MemoryDatabase."""

import asyncio
import logging
from dataclasses import dataclass, field

from papiq.adapters.outbound.memory.database import MemoryDatabase
from papiq.core.ports.event_bus import EventHandler

log = logging.getLogger(__name__)


@dataclass
class _Subscription:
    handler: EventHandler
    cursor: int  # position in the outbox of the next new event
    retries: list[int] = field(default_factory=list)  # positions of failed events


class MemoryEventBus:
    def __init__(self, database: MemoryDatabase) -> None:
        self._db = database
        self._subscriptions: dict[str, _Subscription] = {}
        self._lock = asyncio.Lock()

    def subscribe(self, subscriber: str, handler: EventHandler) -> None:
        if subscriber in self._subscriptions:
            raise ValueError(f"subscriber {subscriber!r} is already registered")
        self._subscriptions[subscriber] = _Subscription(handler, cursor=len(self._db.outbox))

    async def dispatch(self, *, limit: int = 100) -> int:
        async with self._lock:
            delivered = 0
            for name, subscription in self._subscriptions.items():
                delivered += await self._deliver(name, subscription, limit)
            return delivered

    async def _deliver(self, name: str, subscription: _Subscription, limit: int) -> int:
        outbox = self._db.outbox
        pending = [*subscription.retries, *range(subscription.cursor, len(outbox))]
        batch = pending[:limit]
        failed = [position for position in subscription.retries if position not in batch]
        delivered = 0
        for position in batch:
            event = outbox[position]
            try:
                await subscription.handler(event)
            except Exception:
                log.exception(
                    "event handler failed", extra={"subscriber": name, "event_id": str(event.id)}
                )
                failed.append(position)
            else:
                delivered += 1
            subscription.cursor = max(subscription.cursor, position + 1)
        subscription.retries = sorted(failed)
        return delivered
