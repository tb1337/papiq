"""Tests of the SQL adapter beyond the port contracts, run on SQLite and on Postgres.

Test modules subclass the suites and provide the fixtures `database` (migrated, empty),
`uow_factory`, `event_bus_factory` and, for the migration suite, `empty_database`.
"""

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Connection, insert, inspect, select

from papiq.adapters.outbound.memory import ManualClock, MemoryObjectStore
from papiq.adapters.outbound.sql import Database, migrate, schema_state
from papiq.adapters.outbound.sql import tables as t
from papiq.adapters.outbound.sql.migrations import alembic_config
from papiq.adapters.outbound.sql.types import UtcDateTime, json_type
from papiq.core.domain.documents import Document, DocumentChanges
from papiq.core.domain.errors import ConflictError, DuplicateDocumentError
from papiq.core.domain.events import DomainEvent
from papiq.core.domain.fields import FieldDefinition, FieldType, Money
from papiq.core.domain.ids import new_id
from papiq.core.domain.jobs import Job
from papiq.core.domain.pipeline import PIPELINE
from papiq.core.domain.users import User
from papiq.core.ports import DeliveryRetry, EventBus, UnitOfWorkFactory
from papiq.core.services.pipeline import PipelineService, PlaceholderStep
from tests import builders
from tests.builders import NOW, incoming
from tests.contracts.event_bus import EventBusFactory, Recorder, publish, received
from tests.contracts.unit_of_work import owner_with_drawer

LEASE = timedelta(minutes=5)


class SqlAdapterSuite:
    # --- types ----------------------------------------------------------------------------------

    async def test_decimals_keep_their_exact_value_and_scale(
        self, uow_factory: UnitOfWorkFactory
    ) -> None:
        owner, drawer = await owner_with_drawer(uow_factory)
        number = FieldDefinition.create(name="n", data_type=FieldType.NUMBER, now=NOW)
        amount = FieldDefinition.create(name="a", data_type=FieldType.AMOUNT, now=NOW)
        precise = Decimal("12345678901234567890.123456789012345678")
        money = Money(Decimal("0.10"), "EUR")
        document = builders.document(owner, drawer)
        document.apply_changes(
            DocumentChanges(fields={number.id: precise, amount.id: money}),
            {number.id: number, amount.id: amount},
            NOW,
        )
        async with uow_factory() as uow:
            await uow.fields.add(number)
            await uow.fields.add(amount)
            await uow.documents.add(document)
            await uow.commit()
        async with uow_factory() as uow:
            stored = (await uow.documents.get(document.id)).fields
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
            return await pipeline.receive(owner.id, incoming(b"%PDF-1.7 same"), filename="a.pdf")

        results = await asyncio.gather(*(upload() for _ in range(3)), return_exceptions=True)
        assert len([r for r in results if isinstance(r, Document)]) == 1, results
        assert all(isinstance(r, Document | DuplicateDocumentError) for r in results), results
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

    async def test_enqueue_with_dedup_key_many_times(self, uow_factory: UnitOfWorkFactory) -> None:
        """Postgres plans a prepared statement generically from its sixth run on; the partial
        index of the dedup key must still match then."""
        for number in range(12):
            async with uow_factory() as uow:
                assert await uow.jobs.enqueue("test", {}, run_at=NOW, dedup_key=f"k{number}")
                assert (
                    await uow.jobs.enqueue("test", {}, run_at=NOW, dedup_key=f"k{number}") is None
                )
                await uow.commit()

    async def test_dispatch_logs_at_info_level(
        self,
        uow_factory: UnitOfWorkFactory,
        event_bus_factory: EventBusFactory,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Log records must not use reserved field names (`name` raised KeyError)."""
        caplog.set_level(logging.INFO)
        bus = event_bus_factory()
        recorder = Recorder()
        bus.subscribe("index", recorder)
        await publish(uow_factory, received(1))
        assert await bus.dispatch() == 1
        assert any(record.message == "event subscription created" for record in caplog.records)


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

    async def test_schema_state_tells_outdated_current_and_newer(
        self, empty_database: Database
    ) -> None:
        assert await schema_state(empty_database) == "outdated"  # nothing there yet
        await migrate(empty_database, "0001")
        assert await schema_state(empty_database) == "outdated"  # older
        await migrate(empty_database)
        assert await schema_state(empty_database) == "current"
        async with empty_database.writing() as connection:
            await connection.exec_driver_sql("UPDATE alembic_version SET version_num = '0000'")
        assert await schema_state(empty_database) == "newer"  # not a revision of this version

    async def test_attributes_become_fields_and_back(self, empty_database: Database) -> None:
        """Revision 0007 moves definitions, their types and values and renames stored names by
        their place; text a person wrote stays. The downgrade restores them."""
        await migrate(empty_database, "0006")
        ids = {name: new_id() for name in ("user", "drawer", "document", "field", "type", "rule")}
        field, document = str(ids["field"]), str(ids["document"])
        check = {"field": f"attribute:{field}", "outcome": "ok", "reason": "attribute"}
        condition = {"field": "attribute", "op": "is", "value": "x", "attribute_id": field}
        text = {"field": "text", "op": "contains", "value": "attribute"}
        rows: list[tuple[sa.TableClause | sa.Table, dict[str, Any]]] = [
            (t.users, {"id": ids["user"], "username": "a", "username_key": "a", "role": "admin"}),
            (t.drawers, {"id": ids["drawer"], "owner_id": ids["user"], "name_key": "d"}),
            (t.document_types, {"id": ids["type"], "name": "Invoice", "name_key": "invoice"}),
            (_OLD["definitions"], {"id": ids["field"], "name": "Due", "name_key": "due"}),
            (_OLD["types"], {"attribute_id": ids["field"], "document_type_id": ids["type"]}),
            (
                t.documents,
                {
                    "id": ids["document"],
                    "owner_id": ids["user"],
                    "drawer_id": ids["drawer"],
                    "processing_step": "extract_attributes",
                    "processing_outcomes": {"classify": "ok", "extract_attributes": "ok"},
                },
            ),
            (_OLD["values"], {"document_id": ids["document"], "attribute_id": ids["field"]}),
            (
                t.processing_log,
                {
                    "document_id": ids["document"],
                    "step": "extract_attributes",
                    "input": {"prompt": "extract-1"},
                    "output": {"answer": {"attributes": {"a1": "attributes"}}, "fields": [check]},
                },
            ),
            (t.rules, {"id": ids["rule"], "scope": "global", "current_version": 1}),
            (
                t.rule_versions,
                {
                    "rule_id": ids["rule"],
                    "number": 1,
                    "conditions": {"all": [condition, text], "negate": False},
                    "actions": [
                        {"type": "set_attribute", "attribute_id": field, "value": "x"},
                        {"type": "set_title", "title": "set_attribute"},
                    ],
                },
            ),
            (
                t.jobs,
                {
                    "id": new_id(),
                    "kind": "pipeline.step",
                    "payload": {"document_id": document, "step": "extract_attributes", "run": 1},
                    "dedup_key": f"{document}:1:extract_attributes",
                },
            ),
            (
                t.outbox,
                {
                    "event_id": new_id(),
                    "type": "document.updated",
                    "payload": {"document_id": document, "fields": ["title", "attributes"]},
                },
            ),
        ]
        async with empty_database.writing() as connection:
            for table, values in rows:
                columns = table.c
                defaults = {k: v for k, v in _DEFAULTS.items() if k in columns}
                await connection.execute(insert(table).values({**defaults, **values}))
        before = await _stored(empty_database, old=True)

        await migrate(empty_database)
        after = await _stored(empty_database, old=False)
        assert after["definition"] == (ids["field"], "Due", "date")
        assert after["scope"] == (ids["field"], ids["type"])
        assert after["value"] == (ids["document"], ids["field"], "text", "Monday")
        assert after["document"] == ("extract_fields", {"classify": "ok", "extract_fields": "ok"})
        assert after["log"] == (
            "extract_fields",
            {
                "answer": {"fields": {"a1": "attributes"}},
                "fields": [{**check, "field": f"field:{field}"}],
            },
        )
        assert after["rule"] == (
            {
                "all": [{"field": "field", "op": "is", "value": "x", "field_id": field}, text],
                "negate": False,
            },
            [
                {"type": "set_field", "field_id": field, "value": "x"},
                {"type": "set_title", "title": "set_attribute"},
            ],
        )
        assert after["job"] == (
            {"document_id": document, "step": "extract_fields", "run": 1},
            f"{document}:1:extract_fields",
        )
        assert after["event"] == {"document_id": document, "fields": ["title", "fields"]}

        await _downgrade(empty_database, "0006")
        restored = await _stored(empty_database, old=True)
        # The answer's key stays `fields`: checks were stored under `fields` before as well.
        before["log"][1]["answer"] = {"fields": {"a1": "attributes"}}
        assert restored == before

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


_OLD = {
    "definitions": sa.table(
        "attribute_definitions",
        sa.column("id", sa.Uuid()),
        sa.column("name", sa.Text()),
        sa.column("name_key", sa.Text()),
        sa.column("data_type", sa.Text()),
        sa.column("is_global", sa.Boolean()),
        sa.column("choices", json_type()),
        sa.column("created_at", UtcDateTime()),
        sa.column("version", sa.Integer()),
    ),
    "types": sa.table(
        "attribute_document_types",
        sa.column("attribute_id", sa.Uuid()),
        sa.column("document_type_id", sa.Uuid()),
    ),
    "values": sa.table(
        "document_attributes",
        sa.column("document_id", sa.Uuid()),
        sa.column("attribute_id", sa.Uuid()),
        sa.column("kind", sa.Text()),
        sa.column("value_text", sa.Text()),
    ),
}
_DEFAULTS: dict[str, Any] = {
    "created_at": NOW,
    "updated_at": NOW,
    "occurred_at": NOW,
    "recorded_at": NOW,
    "started_at": NOW,
    "run_at": NOW,
    "version": 1,
    "is_default": True,
    "data_type": "date",
    "is_global": False,
    "choices": [],
    "kind": "text",
    "value_text": "Monday",
    "sha256": "0" * 64,
    "title": "T",
    "original_filename": "t.pdf",
    "media_type": "application/pdf",
    "channel": "api",
    "processing_status": "running",
    "processing_run": 1,
    "processing_outcomes": {},
    "run": 1,
    "outcome": "ok",
    "pipeline_version": "1",
    "duration_us": 1,
    "enabled": True,
    "name": "N",
    "priority": 1,
    "triggers": ["ingest"],
    "status": "queued",
    "attempts": 0,
    "releases": 0,
}


async def _stored(database: Database, *, old: bool) -> dict[str, Any]:
    """The rows of `test_attributes_become_fields_and_back`, under the old or new names."""
    prefix = "attribute" if old else "field"
    id = sa.column(f"{prefix}_id", sa.Uuid())
    definitions = sa.table(
        f"{prefix}_definitions",
        sa.column("id", sa.Uuid()),
        sa.column("name", sa.Text()),
        sa.column("data_type", sa.Text()),
    )
    types = sa.table(f"{prefix}_document_types", id, sa.column("document_type_id", sa.Uuid()))
    values = sa.table(
        "document_attributes" if old else "document_fields",
        sa.column("document_id", sa.Uuid()),
        sa.column(id.name, sa.Uuid()),
        sa.column("kind", sa.Text()),
        sa.column("value_text", sa.Text()),
    )
    async with database.reading() as connection:

        async def one(table: Any) -> Any:
            return (await connection.execute(select(table))).one()

        definition, scope, value = await one(definitions), await one(types), await one(values)
        document, log = await one(t.documents), await one(t.processing_log)
        rule, job, event = await one(t.rule_versions), await one(t.jobs), await one(t.outbox)
    return {
        "definition": tuple(definition),
        "scope": tuple(scope),
        "value": tuple(value),
        "document": (document.processing_step, document.processing_outcomes),
        "log": (log.step, log.output),
        "rule": (rule.conditions, rule.actions),
        "job": (job.payload, job.dedup_key),
        "event": event.payload,
    }


async def _downgrade(database: Database, revision: str) -> None:
    async with database.engine.connect() as connection:
        await connection.run_sync(lambda sync: command.downgrade(alembic_config(sync), revision))
        await connection.commit()


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
