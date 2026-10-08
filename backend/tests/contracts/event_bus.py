from datetime import UTC, datetime, timedelta
from typing import Protocol

import pytest

from papiq.adapters.outbound.memory import ManualClock
from papiq.core.domain.events import (
    EVENT_TYPES,
    DocumentDeleted,
    DocumentFiled,
    DocumentReceived,
    DocumentUpdated,
    DomainEvent,
    LaneChanged,
    StepCompleted,
)
from papiq.core.domain.ids import DocumentId, DrawerId, UserId, new_id
from papiq.core.domain.pipeline import Lane, Outcome, Step
from papiq.core.ports import Clock, DeliveryRetry, EventBus, UnitOfWorkFactory
from papiq.core.ports.event_bus import DEFAULT_DELIVERY_RETRY
from tests.builders import NOW

# Failed deliveries are due again at once: most tests are about what is delivered, not when.
IMMEDIATE = DeliveryRetry(delay=timedelta(0))
FAR_FUTURE = datetime(2100, 1, 1, tzinfo=UTC)


class EventBusFactory(Protocol):
    """Creates a bus on the same database as `uow_factory`."""

    def __call__(
        self, *, clock: Clock | None = None, retry: DeliveryRetry = DEFAULT_DELIVERY_RETRY
    ) -> EventBus: ...


class Recorder:
    def __init__(self, failures: int = 0) -> None:
        self.received: list[DomainEvent] = []
        self.failures = failures

    async def __call__(self, event: DomainEvent) -> None:
        if self.failures:
            self.failures -= 1
            raise RuntimeError("handler failed")
        self.received.append(event)


def received(n: int = 0) -> DocumentReceived:
    return DocumentReceived(
        document_id=DocumentId(new_id()), occurred_at=NOW + timedelta(seconds=n)
    )


async def publish(uow_factory: UnitOfWorkFactory, *events: DomainEvent) -> None:
    async with uow_factory() as uow:
        await uow.outbox.add(events)
        await uow.commit()


class EventBusContract:
    """Needs the fixtures `uow_factory` and `event_bus_factory` (new bus instances on the same
    database)."""

    @pytest.fixture
    def event_bus(self, event_bus_factory: EventBusFactory) -> EventBus:
        return event_bus_factory(retry=IMMEDIATE)

    async def test_committed_events_are_delivered_once(
        self, uow_factory: UnitOfWorkFactory, event_bus: EventBus
    ) -> None:
        recorder = Recorder()
        event_bus.subscribe("test", recorder)
        events = [received(n) for n in range(3)]
        await publish(uow_factory, *events[:2])
        await publish(uow_factory, events[2])
        assert await event_bus.dispatch() == 3
        assert recorder.received == events
        assert await event_bus.dispatch() == 0
        assert recorder.received == events

    async def test_every_event_type_survives_delivery(
        self, uow_factory: UnitOfWorkFactory, event_bus: EventBus
    ) -> None:
        recorder = Recorder()
        event_bus.subscribe("test", recorder)
        document = DocumentId(new_id())
        events: list[DomainEvent] = [
            DocumentReceived(document_id=document, occurred_at=NOW),
            StepCompleted(
                document_id=document, occurred_at=NOW, step=Step.OCR, run=2, outcome=Outcome.OK
            ),
            LaneChanged(document_id=document, occurred_at=NOW, old=None, new=Lane.YELLOW),
            LaneChanged(document_id=document, occurred_at=NOW, old=Lane.RED, new=None),
            DocumentFiled(document_id=document, occurred_at=NOW, drawer_id=DrawerId(new_id())),
            DocumentUpdated(document_id=document, occurred_at=NOW, fields=("title", "attributes")),
            DocumentDeleted(document_id=document, occurred_at=NOW),
            DocumentDeleted(
                document_id=document,
                occurred_at=NOW,
                readers=(UserId(new_id()), UserId(new_id())),
            ),
        ]
        assert {type(event) for event in events} == set(EVENT_TYPES.values())
        await publish(uow_factory, *events)
        await event_bus.dispatch()
        assert recorder.received == events

    async def test_uncommitted_events_are_not_delivered(
        self, uow_factory: UnitOfWorkFactory, event_bus: EventBus
    ) -> None:
        recorder = Recorder()
        event_bus.subscribe("test", recorder)
        async with uow_factory() as uow:
            await uow.outbox.add([received()])
        assert await event_bus.dispatch() == 0
        assert recorder.received == []

    async def test_every_subscriber_gets_every_event(
        self, uow_factory: UnitOfWorkFactory, event_bus: EventBus
    ) -> None:
        index, webhooks = Recorder(), Recorder()
        event_bus.subscribe("index", index)
        event_bus.subscribe("webhooks", webhooks)
        event = received()
        await publish(uow_factory, event)
        assert await event_bus.dispatch() == 2
        assert index.received == webhooks.received == [event]

    async def test_failed_delivery_is_repeated(
        self, uow_factory: UnitOfWorkFactory, event_bus: EventBus
    ) -> None:
        flaky, steady = Recorder(failures=1), Recorder()
        event_bus.subscribe("flaky", flaky)
        event_bus.subscribe("steady", steady)
        first, second = received(1), received(2)
        await publish(uow_factory, first, second)
        assert await event_bus.dispatch() == 3
        assert flaky.received == [second]
        assert steady.received == [first, second]
        assert await event_bus.dispatch() == 1
        assert flaky.received == [second, first]
        assert await event_bus.dispatch() == 0

    async def test_permanently_failing_events_do_not_block_later_ones(
        self, uow_factory: UnitOfWorkFactory, event_bus: EventBus
    ) -> None:
        good = received(9)

        async def handler(event: DomainEvent) -> None:
            if event != good:
                raise RuntimeError("poison")
            delivered.append(event)

        delivered: list[DomainEvent] = []
        event_bus.subscribe("test", handler)
        await publish(uow_factory, *(received(n) for n in range(3)))
        await event_bus.dispatch(limit=2)
        await event_bus.dispatch(limit=2)
        await publish(uow_factory, good)
        await event_bus.dispatch(limit=2)
        assert delivered == [good]

    async def test_events_that_keep_failing_do_not_starve_other_failures(
        self, uow_factory: UnitOfWorkFactory, event_bus: EventBus
    ) -> None:
        poison = [received(1), received(2)]
        flaky = received(3)
        flaky_failures = 1
        delivered: list[DomainEvent] = []

        async def handler(event: DomainEvent) -> None:
            nonlocal flaky_failures
            if event in poison:
                raise RuntimeError("poison")
            if flaky_failures:
                flaky_failures -= 1
                raise RuntimeError("flaky")
            delivered.append(event)

        event_bus.subscribe("test", handler)
        await publish(uow_factory, *poison, flaky)
        for _ in range(5):
            await event_bus.dispatch(limit=2)
        assert delivered == [flaky]

    async def test_a_late_commit_is_not_skipped(
        self, uow_factory: UnitOfWorkFactory, event_bus: EventBus
    ) -> None:
        recorder = Recorder()
        event_bus.subscribe("test", recorder)
        early, late = received(1), received(2)
        async with uow_factory() as slow:
            await slow.outbox.add([early])
            await publish(uow_factory, late)
            assert await event_bus.dispatch() == 1
            await slow.commit()
        assert await event_bus.dispatch() == 1
        assert recorder.received == [late, early]

    async def test_a_new_bus_resumes_the_subscription(
        self, uow_factory: UnitOfWorkFactory, event_bus_factory: EventBusFactory
    ) -> None:
        before = event_bus_factory()
        first = Recorder()
        before.subscribe("index", first)
        delivered, missed = received(1), received(2)
        await publish(uow_factory, delivered)
        await before.dispatch()
        await publish(uow_factory, missed)  # committed while no bus runs

        after = event_bus_factory()
        second = Recorder()
        after.subscribe("index", second)
        await after.dispatch()
        assert first.received == [delivered]
        assert second.received == [missed]

    async def test_subscribers_receive_events_from_subscription_on(
        self, uow_factory: UnitOfWorkFactory, event_bus: EventBus
    ) -> None:
        await publish(uow_factory, received(1))
        recorder = Recorder()
        event_bus.subscribe("late", recorder)
        later = received(2)
        await publish(uow_factory, later)
        await event_bus.dispatch()
        assert recorder.received == [later]

    async def test_limit(self, uow_factory: UnitOfWorkFactory, event_bus: EventBus) -> None:
        recorder = Recorder()
        event_bus.subscribe("test", recorder)
        events = [received(n) for n in range(5)]
        await publish(uow_factory, *events)
        assert await event_bus.dispatch(limit=2) == 2
        assert await event_bus.dispatch(limit=2) == 2
        assert await event_bus.dispatch(limit=2) == 1
        assert recorder.received == events

    def test_subscriber_names_are_unique(self, event_bus: EventBus) -> None:
        event_bus.subscribe("test", Recorder())
        with pytest.raises(ValueError, match="already"):
            event_bus.subscribe("test", Recorder())

    # --- delays and limits of repeated deliveries -----------------------------------------------

    async def test_a_failed_delivery_waits_with_growing_delay_then_is_given_up(
        self, uow_factory: UnitOfWorkFactory, event_bus_factory: EventBusFactory
    ) -> None:
        clock = ManualClock(NOW)
        bus = event_bus_factory(
            clock=clock, retry=DeliveryRetry(max_attempts=3, delay=timedelta(seconds=10))
        )
        attempts = 0

        async def failing(event: DomainEvent) -> None:
            nonlocal attempts
            attempts += 1
            raise RuntimeError("receiver down")

        steady = Recorder()
        bus.subscribe("failing", failing)
        bus.subscribe("steady", steady)
        await publish(uow_factory, received())
        await bus.dispatch()
        assert attempts == 1

        for wait, expected in [(9, 1), (1, 2), (19, 2), (1, 3), (3600, 3)]:
            clock.advance(timedelta(seconds=wait))
            await bus.dispatch()
            assert attempts == expected, f"after {wait} s more"
        assert len(steady.received) == 1

    async def test_a_late_success_ends_the_repetitions(
        self, uow_factory: UnitOfWorkFactory, event_bus_factory: EventBusFactory
    ) -> None:
        clock = ManualClock(NOW)
        bus = event_bus_factory(clock=clock, retry=DeliveryRetry(delay=timedelta(seconds=1)))
        flaky = Recorder(failures=2)
        bus.subscribe("flaky", flaky)
        event = received()
        await publish(uow_factory, event)
        for _ in range(5):
            await bus.dispatch()
            clock.advance(timedelta(minutes=1))
        assert flaky.received == [event]

    # --- purge ----------------------------------------------------------------------------------

    async def test_purge_removes_events_every_subscriber_has_handled(
        self, uow_factory: UnitOfWorkFactory, event_bus: EventBus
    ) -> None:
        index, webhooks = Recorder(), Recorder(failures=1)
        event_bus.subscribe("index", index)
        event_bus.subscribe("webhooks", webhooks)
        await event_bus.dispatch()  # creates the subscriptions
        first, second = received(1), received(2)
        await publish(uow_factory, first, second)
        assert await event_bus.purge(before=FAR_FUTURE) == 0  # not delivered yet

        await event_bus.dispatch()  # `first` failed for webhooks and waits
        assert await event_bus.purge(before=datetime(2000, 1, 1, tzinfo=UTC)) == 0
        assert await event_bus.purge(before=FAR_FUTURE) == 1
        await event_bus.dispatch()
        assert webhooks.received == [second, first]
        assert await event_bus.purge(before=FAR_FUTURE) == 1
        assert await event_bus.purge(before=FAR_FUTURE) == 0

        later = received(3)
        await publish(uow_factory, later)
        await event_bus.dispatch()
        assert index.received == [first, second, later]

    async def test_purge_counts_given_up_events_as_handled(
        self, uow_factory: UnitOfWorkFactory, event_bus_factory: EventBusFactory
    ) -> None:
        bus = event_bus_factory(retry=DeliveryRetry(max_attempts=1))

        async def failing(event: DomainEvent) -> None:
            raise RuntimeError("receiver down")

        bus.subscribe("failing", failing)
        await bus.dispatch()  # creates the subscription
        await publish(uow_factory, received())
        await bus.dispatch()
        assert await bus.purge(before=FAR_FUTURE) == 1

    async def test_without_subscriptions_every_event_is_handled(
        self, uow_factory: UnitOfWorkFactory, event_bus: EventBus
    ) -> None:
        await publish(uow_factory, received(1), received(2))
        assert await event_bus.purge(before=FAR_FUTURE) == 2

    async def test_purged_events_do_not_disturb_a_resumed_subscription(
        self, uow_factory: UnitOfWorkFactory, event_bus_factory: EventBusFactory
    ) -> None:
        first = event_bus_factory(retry=IMMEDIATE)
        first.subscribe("index", Recorder())
        await publish(uow_factory, received(1))
        await first.dispatch()
        assert await first.purge(before=FAR_FUTURE) == 1

        later = received(2)
        await publish(uow_factory, later)
        second = event_bus_factory(retry=IMMEDIATE)
        recorder = Recorder()
        second.subscribe("index", recorder)
        await second.dispatch()
        assert recorder.received == [later]
