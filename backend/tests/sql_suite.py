"""Tests of the SQL adapter beyond the port contracts, run on SQLite and on Postgres.

Test modules subclass the suites and provide the fixtures `database` (migrated, empty),
`uow_factory`, `event_bus_factory` and, for the migration suite, `empty_database`.
"""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Connection, insert, inspect, select

from papiq.adapters.outbound.memory import ManualClock, MemoryObjectStore
from papiq.adapters.outbound.sql import Database, migrate
from papiq.adapters.outbound.sql import tables as t
from papiq.core.domain.attributes import AttributeDefinition, AttributeType, Money
from papiq.core.domain.documents import Document, DocumentChanges
from papiq.core.domain.errors import ConflictError
from papiq.core.domain.events import DomainEvent
from papiq.core.domain.ids import new_id
from papiq.core.domain.jobs import Job
from papiq.core.domain.pipeline import PIPELINE
from papiq.core.domain.users import User
from papiq.core.ports import DeliveryRetry, EventBus, UnitOfWorkFactory
from papiq.core.services.pipeline import PipelineService, PlaceholderStep
from tests import builders
from tests.builders import NOW
from tests.contracts.event_bus import EventBusFactory, Recorder, publish, received
from tests.contracts.unit_of_work import owner_with_drawer

LEASE = timedelta(minutes=5)


class SqlAdapterSuite:
    # --- types ----------------------------------------------------------------------------------

    async def test_decimals_keep_their_exact_value_and_scale(
        self, uow_factory: UnitOfWorkFactory
    ) -> None:
        owner, drawer = await owner_with_drawer(uow_factory)
        number = AttributeDefinition.create(name="n", data_type=AttributeType.NUMBER, now=NOW)
        amount = AttributeDefinition.create(name="a", data_type=AttributeType.AMOUNT, now=NOW)
        precise = Decimal("12345678901234567890.123456789012345678")
        money = Money(Decimal("0.10"), "EUR")
        document = builders.document(owner, drawer)
        document.apply_changes(
            DocumentChanges(attributes={number.id: precise, amount.id: money}),
            {number.id: number, amount.id: amount},
            NOW,
        )
        async with uow_factory() as uow:
            await uow.attributes.add(number)
            await uow.attributes.add(amount)
            await uow.documents.add(document)
            await uow.commit()
        async with uow_factory() as uow:
            stored = (await uow.documents.get(document.id)).attributes
        assert str(stored[number.id]) == str(precise)
        stored_money = stored[amount.id]
        assert isinstance(stored_money, Money)
        assert str(stored_money.amount) == "0.10"  # scale kept, no binary float

    async def test_timestamps_are_utc_with_microseconds(
        self, uow_factory: UnitOfWorkFactory
    ) -> None:
        moment = datetime(2026, 3, 29, 1, 30, 15, 123456, tzinfo=UTC)
        async with uow_factory() as uow:
            id = await uow.jobs.enqueue("test", {}, run_at=moment)
            await uow.commit()
        assert id is not None
        async with uow_factory() as uow:
            job = await uow.jobs.get(id)
        assert job.run_at == moment
        assert job.run_at.tzinfo is UTC

    async def test_due_jobs_compare_as_instants(self, uow_factory: UnitOfWorkFactory) -> None:
        """Comparisons in SQL follow time, also across days, months and microseconds."""
        times = [
            datetime(2026, 12, 31, 23, 59, 59, 999999, tzinfo=UTC),
            datetime(2027, 1, 1, tzinfo=UTC),
            datetime(2027, 1, 1, 0, 0, 0, 1, tzinfo=UTC),
        ]
        async with uow_factory() as uow:
            for moment in reversed(times):
                await uow.jobs.enqueue("test", {}, run_at=moment)
            await uow.commit()
        claimed: list[datetime] = []
        for now in times:
            async with uow_factory() as uow:
                job = await uow.jobs.claim(now=now, lease=LEASE)
                assert job is not None
                claimed.append(job.run_at)
                assert await uow.jobs.claim(now=now, lease=LEASE) is None
                await uow.commit()
        assert claimed == times

    # --- concurrency ----------------------------------------------------------------------------

    async def test_many_workers_claim_every_job_exactly_once(
        self, uow_factory: UnitOfWorkFactory
    ) -> None:
        async with uow_factory() as uow:
            enqueued = {await uow.jobs.enqueue("test", {"n": n}, run_at=NOW) for n in range(30)}
            await uow.commit()

        async def worker() -> list[Job]:
            claimed: list[Job] = []
            while True:
                async with uow_factory() as uow:
                    job = await uow.jobs.claim(now=NOW, lease=LEASE)
                    await uow.commit()
                if job is None:
                    return claimed
                claimed.append(job)
                async with uow_factory() as uow:
                    await uow.jobs.complete(job)
                    await uow.commit()

        results = await asyncio.gather(*(worker() for _ in range(6)))
        claimed = [job.id for jobs in results for job in jobs]
        assert None not in enqueued
        assert sorted(claimed) == sorted(id for id in enqueued if id)  # each job exactly once

    async def test_concurrent_duplicate_originals_conflict_in_the_database(
        self, uow_factory: UnitOfWorkFactory
    ) -> None:
        """Both units find no duplicate, then both add the same original: the unique index
        rejects one of them."""
        owner, drawer = await owner_with_drawer(uow_factory)
        both_checked = asyncio.Barrier(2)

        async def add() -> Document:
            document = builders.document(owner, drawer, content="same")
            async with uow_factory() as uow:
                assert await uow.documents.find_by_sha256(owner.id, document.sha256) is None
                async with asyncio.timeout(10):
                    await both_checked.wait()
                await uow.documents.add(document)
                await uow.commit()
            return document

        results = await asyncio.gather(add(), add(), return_exceptions=True)
        assert len([r for r in results if isinstance(r, Document)]) == 1
        assert len([r for r in results if isinstance(r, ConflictError)]) == 1

    async def test_concurrent_uploads_of_the_same_file_create_one_document(
        self, uow_factory: UnitOfWorkFactory
    ) -> None:
        owner, _ = await owner_with_drawer(uow_factory)
        pipeline = PipelineService(
            uow_factory,
            ManualClock(NOW),
            MemoryObjectStore(),
            {step: PlaceholderStep() for step in PIPELINE[1:]},
            pipeline_version="test",
        )

        async def upload() -> Document:
            return await pipeline.receive(
                owner.id, b"%PDF same", filename="a.pdf", media_type="application/pdf"
            )

        results = await asyncio.gather(*(upload() for _ in range(3)), return_exceptions=True)
        assert len([r for r in results if isinstance(r, Document)]) == 1
        assert all(isinstance(r, Document | ConflictError) for r in results), results
        async with uow_factory() as uow:
            assert len(await uow.documents.list_visible_to(owner.id)) == 1

    async def test_a_failed_write_leaves_only_rollback(
        self, uow_factory: UnitOfWorkFactory
    ) -> None:
        user = builders.user()
        await _seed_user(uow_factory, user)
        async with uow_factory() as uow:
            with pytest.raises(ConflictError):
                await uow.users.add(user)
            with pytest.raises(RuntimeError):
                await uow.users.find(user.id)
            with pytest.raises(RuntimeError):
                await uow.commit()

    # --- events ---------------------------------------------------------------------------------

    async def test_handlers_may_write_while_dispatching(
        self, uow_factory: UnitOfWorkFactory, event_bus_factory: Callable[[], EventBus]
    ) -> None:
        """Handlers run outside of the dispatcher's transactions: on SQLite, a handler that
        writes must not wait for the write lock of its own dispatcher."""
        bus = event_bus_factory()

        async def index(event: DomainEvent) -> None:
            async with uow_factory() as uow:
                await uow.jobs.enqueue("index", {"event": str(event.id)}, run_at=NOW)
                await uow.commit()

        bus.subscribe("index", index)
        await publish(uow_factory, received(1), received(2))
        assert await bus.dispatch() == 2
        async with uow_factory() as uow:
            assert await uow.jobs.claim(now=NOW, lease=LEASE, kinds=["index"]) is not None

    async def test_events_of_unknown_type_are_skipped(
        self,
        uow_factory: UnitOfWorkFactory,
        event_bus_factory: Callable[[], EventBus],
        database: Database,
    ) -> None:
        """An event type written by a newer version is skipped, not retried forever."""
        bus = event_bus_factory()
        recorder = Recorder()
        bus.subscribe("test", recorder)
        await bus.dispatch()  # creates the subscription
        async with database.writing() as connection:
            await connection.execute(
                insert(t.outbox).values(
                    event_id=new_id(),
                    type="document.from_the_future",
                    occurred_at=NOW,
                    payload={},
                    recorded_at=datetime.now(UTC),
                )
            )
        known = received(1)
        await publish(uow_factory, known)
        assert await bus.dispatch() == 1
        assert await bus.dispatch() == 0
        assert recorder.received == [known]
        async with database.reading() as connection:
            assert (await connection.execute(select(t.event_retries))).all() == []

    async def test_failed_deliveries_are_kept_per_subscriber(
        self,
        uow_factory: UnitOfWorkFactory,
        event_bus_factory: EventBusFactory,
        database: Database,
    ) -> None:
        clock = ManualClock(NOW)
        bus = event_bus_factory(clock=clock, retry=DeliveryRetry(delay=timedelta(seconds=10)))
        failing = Recorder(failures=3)
        bus.subscribe("failing", failing)
        await publish(uow_factory, received(1))
        await bus.dispatch()
        clock.advance(timedelta(seconds=10))
        await bus.dispatch()
        async with database.reading() as connection:
            rows = (await connection.execute(select(t.event_retries))).all()
        assert [(row.subscriber, row.attempts) for row in rows] == [("failing", 2)]
        assert "handler failed" in rows[0].last_error
        assert rows[0].retry_at == NOW + timedelta(seconds=30)


class MigrationSuite:
    """Needs the fixtures `empty_database` and `model_database`: two databases without
    schema."""

    async def test_upgrade_creates_the_schema_on_an_empty_database(
        self, empty_database: Database
    ) -> None:
        await migrate(empty_database)
        await migrate(empty_database)  # already up to date: nothing to do
        async with empty_database.reading() as connection:
            names = await connection.run_sync(lambda sync: inspect(sync).get_table_names())
        assert set(names) == {*t.metadata.tables, "alembic_version"}

    async def test_migrations_match_the_table_definitions(
        self, empty_database: Database, model_database: Database
    ) -> None:
        """Alembic's comparison, plus the DDL as the database reports it: Alembic does not
        compare partial-index predicates or SQLite's AUTOINCREMENT."""
        await migrate(empty_database)
        async with empty_database.reading() as connection:
            differences = await connection.run_sync(_compare)
            migrated = await connection.run_sync(_schema)
        assert differences == []
        async with model_database.writing() as connection:
            await connection.run_sync(t.metadata.create_all)
        async with model_database.reading() as connection:
            modelled = await connection.run_sync(_schema)
        assert migrated == modelled


def _compare(connection: Connection) -> list[Any]:
    context = MigrationContext.configure(connection, opts={"compare_type": True})
    return list(compare_metadata(context, t.metadata))


def _schema(connection: Connection) -> set[tuple[Any, ...]]:
    """Tables, columns, constraints and indexes as the database describes them."""
    if connection.dialect.name == "sqlite":
        query = (
            "SELECT type, name, sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' AND name NOT LIKE '%alembic_version%'"
        )
        return {
            (kind, name, _table_clauses(sql) if kind == "table" else " ".join(sql.split()))
            for kind, name, sql in connection.exec_driver_sql(query)
        }
    queries = [
        "SELECT 'index', indexname, indexdef FROM pg_indexes WHERE schemaname = 'public'",
        "SELECT 'constraint', conname, pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE connamespace = 'public'::regnamespace",
        "SELECT 'column', table_name || '.' || column_name, "
        "concat_ws(' ', data_type, numeric_precision, numeric_scale, character_maximum_length, "
        "is_nullable, column_default) FROM information_schema.columns "
        "WHERE table_schema = 'public'",
    ]
    return {
        tuple(row)
        for query in queries
        for row in connection.exec_driver_sql(query)
        if "alembic_version" not in row[1]
    }


def _table_clauses(sql: str) -> tuple[str, frozenset[str]]:
    """CREATE TABLE as its head and its clauses (split at top-level commas), order ignored."""
    head, body = " ".join(sql.split()).split("(", 1)
    clauses, depth, current = [], 0, ""
    for char in body.rsplit(")", 1)[0]:
        depth += {"(": 1, ")": -1}.get(char, 0)
        if char == "," and depth == 0:
            clauses.append(current.strip())
            current = ""
        else:
            current += char
    return head.strip(), frozenset([*clauses, current.strip()])


async def _seed_user(uow_factory: UnitOfWorkFactory, user: User) -> None:
    async with uow_factory() as uow:
        await uow.users.add(user)
        await uow.commit()
