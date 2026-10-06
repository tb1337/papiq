from collections.abc import Awaitable, Callable, Iterable
from typing import Protocol

from papiq.core.domain.events import DomainEvent

type EventHandler = Callable[[DomainEvent], Awaitable[None]]


class Outbox(Protocol):
    """Writing side of the event bus, part of the unit of work: events are stored in the same
    transaction as the state change (transactional outbox) and exist only after commit."""

    async def add(self, events: Iterable[DomainEvent]) -> None: ...


class EventBus(Protocol):
    """Delivers committed events to subscribers, at least once per subscriber.

    - A subscriber receives the events committed after it subscribed, in commit order.
    - If its handler raises, that event is delivered to it again on a later dispatch; later
      events are still delivered. Receivers recognise repetitions by the event id.

    First adapter: outbox table in the database with in-process dispatch (M2).
    Later adapters: Valkey Streams, NATS.
    """

    def subscribe(self, subscriber: str, handler: EventHandler) -> None:
        """Register a handler under a unique subscriber name."""
        ...

    async def dispatch(self, *, limit: int = 100) -> int:
        """Deliver up to `limit` pending events to each subscriber; returns the number of
        successful deliveries."""
        ...
