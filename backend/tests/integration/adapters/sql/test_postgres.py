"""The SQL adapters on Postgres pass every contract suite and the SQL suites."""

import asyncio
import random
from collections.abc import Callable

import pytest
from sqlalchemy import select

from papiq.adapters.outbound.sql import Database
from papiq.adapters.outbound.sql import tables as t
from papiq.adapters.outbound.sql.unit_of_work import SqlOutbox
from papiq.core.domain.errors import ConcurrencyError
from papiq.core.domain.events import DomainEvent
from papiq.core.domain.users import User
from papiq.core.ports import EventBus, UnitOfWorkFactory
from tests import builders
from tests.contracts.event_bus import EventBusContract, Recorder, publish, received
from tests.contracts.identity import IdentityRepositoriesContract
from tests.contracts.job_queue import JobQueueContract
from tests.contracts.unit_of_work import UnitOfWorkContract
from tests.sql_suite import MigrationSuite, SqlAdapterSuite


class TestPostgresUnitOfWork(UnitOfWorkContract):
    pass


class TestPostgresIdentityRepositories(IdentityRepositoriesContract):
    pass


class TestPostgresJobQueue(JobQueueContract):
    pass


class TestPostgresEventBus(EventBusContract):
    pass


class TestPostgresAdapter(SqlAdapterSuite):
    pass


class TestPostgresMigrations(MigrationSuite):
    pass


# --- events of transactions that commit out of order ---------------------------------------------


async def test_an_event_written_first_but_committed_last_is_not_skipped(
    uow_factory: UnitOfWorkFactory,
    event_bus_factory: Callable[[], EventBus],
    database: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The case a plain position per subscriber loses events: the slow transaction writes its
    event (drawing the lower `seq`), then a fast transaction writes and commits, a dispatch
    delivers the fast event and moves the position past the slow one, which commits last.

    The slow transaction is held between writing its outbox rows and COMMIT. The fast one must
    wait for it (advisory lock), so the slow event is committed first and nothing is skipped.
    """
    bus = event_bus_factory()
    recorder = Recorder()
    bus.subscribe("test", recorder)
    early, late = received(1), received(2)
    flushed, release = asyncio.Event(), asyncio.Event()
    flush = SqlOutbox.flush

    async def flush_and_hold(outbox: SqlOutbox) -> None:
        held = early in outbox.pending
        await flush(outbox)
        if held:
            flushed.set()
            await release.wait()

    monkeypatch.setattr(SqlOutbox, "flush", flush_and_hold)

    async def slow() -> None:
        async with uow_factory() as uow:
            await uow.outbox.add([early])
            await uow.commit()

    slow_task = asyncio.create_task(slow())
    async with asyncio.timeout(10):
        await flushed.wait()
    fast_task = asyncio.create_task(publish(uow_factory, late))
    await asyncio.wait([fast_task], timeout=0.3)
    fast_waited = not fast_task.done()
    await bus.dispatch()  # without the lock: delivers `late` and moves past `early`
    release.set()
    await asyncio.gather(slow_task, fast_task)
    while await bus.dispatch():
        pass
    assert recorder.received == [early, late]
    assert fast_waited  # for the slow transaction's commit
    async with database.reading() as connection:
        order = await connection.execute(select(t.outbox.c.event_id).order_by(t.outbox.c.seq))
        assert order.scalars().all() == [early.id, late.id]


async def test_concurrent_publishers_lose_no_events(
    uow_factory: UnitOfWorkFactory, event_bus_factory: Callable[[], EventBus]
) -> None:
    """End to end under load: many transactions write state and events and commit in random
    order while a dispatcher runs; every event reaches the subscriber exactly once. (The
    out-of-order commit itself is produced deterministically by the test above.)"""
    bus = event_bus_factory()
    delivered: list[DomainEvent] = []

    async def record(event: DomainEvent) -> None:
        delivered.append(event)

    bus.subscribe("test", record)
    published: list[DomainEvent] = []
    rng = random.Random(7)

    async def publisher(n: int) -> None:
        for round in range(5):
            events = [received(n * 100 + round * 10 + i) for i in range(rng.randint(1, 3))]
            async with uow_factory() as uow:
                await uow.users.add(builders.user())
                await asyncio.sleep(rng.random() / 100)
                await uow.outbox.add(events)
                await asyncio.sleep(rng.random() / 100)
                await uow.commit()
            published.extend(events)

    async def dispatcher(done: asyncio.Event) -> None:
        while not done.is_set():
            await bus.dispatch(limit=7)
            await asyncio.sleep(0.001)

    done = asyncio.Event()
    running = asyncio.create_task(dispatcher(done))
    await asyncio.gather(*(publisher(n) for n in range(12)))
    done.set()
    await running
    while await bus.dispatch():
        pass
    assert sorted(event.id for event in delivered) == sorted(event.id for event in published)


async def test_a_deadlock_is_a_concurrency_error(uow_factory: UnitOfWorkFactory) -> None:
    """Two units update the same two rows in opposite order; Postgres aborts one of them."""
    first, second = builders.user(), builders.user()
    async with uow_factory() as uow:
        await uow.users.add(first)
        await uow.users.add(second)
        await uow.commit()
    both_updated_one = asyncio.Barrier(2)

    async def rename_both(one: User, other: User) -> None:
        async with uow_factory() as uow:
            a, b = await uow.users.get(one.id), await uow.users.get(other.id)
            a.username += "-a"
            await uow.users.update(a)
            async with asyncio.timeout(10):
                await both_updated_one.wait()
            b.username += "-b"
            await uow.users.update(b)
            await uow.commit()

    results = await asyncio.gather(
        rename_both(first, second), rename_both(second, first), return_exceptions=True
    )
    assert [type(result) for result in results].count(ConcurrencyError) == 1
    assert results.count(None) == 1
