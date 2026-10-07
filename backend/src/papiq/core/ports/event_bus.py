from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from papiq.core.domain.events import DomainEvent

type EventHandler = Callable[[DomainEvent], Awaitable[None]]


class Outbox(Protocol):
    """Writing side of the event bus, part of the unit of work: events are stored in the same
    transaction as the state change (transactional outbox) and exist only after commit."""

    async def add(self, events: Iterable[DomainEvent]) -> None: ...


class EventBus(Protocol):
    """Delivers committed events to subscribers, at least once per subscriber.

    - Subscriptions are durable by name: the first registration of a name receives the events
      committed from then on; a later registration under the same name (e.g. after a restart,
      on a new bus instance) resumes where the previous one stopped.
    - No committed event is skipped, also not one whose transaction started before, but
      committed after, events that were already delivered.
    - Events of one transaction arrive in the order they were added; across transactions the
      order is not guaranteed. Events are thin: receivers fetch the current state.
    - If a handler raises, that event is delivered to it again on a later dispatch, after a
      growing delay (`DeliveryRetry`); it does not hold back later events. After the last
      attempt the event is given up for that subscriber and logged as an error. Receivers
      recognise repetitions by the event id.

    First adapter: outbox table in the database with in-process dispatch (M2).
    Later adapters: Valkey Streams, NATS.
    """

    def subscribe(self, subscriber: str, handler: EventHandler) -> None:
        """Register a handler under a subscriber name, unique per bus instance. Adapters may
        create the durable subscription lazily on the next dispatch."""
        ...

    async def dispatch(self, *, limit: int = 100) -> int:
        """Deliver to each subscriber up to `limit` new events and up to `limit` events that
        failed before and are due again; returns the number of successful deliveries."""
        ...

    async def purge(self, *, before: datetime) -> int:
        """Remove events recorded before `before` that every subscription has handled (delivered
        or given up); returns how many. Without subscriptions, every such event counts as
        handled. Keep `before` well in the past: a subscription is created on its first
        dispatch and only then holds back events."""
        ...


@dataclass(frozen=True)
class DeliveryRetry:
    """Repeated delivery of events whose handler failed: at most `max_attempts` attempts in
    total; before attempt n+1 the bus waits `delay * 2^(n-1)`, at most `max_delay`."""

    max_attempts: int = 10
    delay: timedelta = timedelta(seconds=1)
    max_delay: timedelta = timedelta(hours=1)

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")

    def next_attempt(self, attempts: int, now: datetime) -> datetime | None:
        """When to try again after `attempts` failed attempts; None to give up."""
        if attempts >= self.max_attempts:
            return None
        return now + min(self.delay * (1 << min(attempts - 1, 40)), self.max_delay)


DEFAULT_DELIVERY_RETRY = DeliveryRetry()
