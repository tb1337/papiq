"""The SQL adapters on SQLite pass every contract suite and the SQL suites."""

import asyncio
from decimal import Decimal

import pytest
from sqlalchemy import text

from papiq.adapters.outbound.sql import Database
from papiq.core.domain.documents import DocumentChanges
from papiq.core.domain.fields import FieldDefinition, FieldType, Money
from papiq.core.ports import UnitOfWorkFactory
from tests import builders
from tests.builders import NOW
from tests.contracts.event_bus import EventBusContract
from tests.contracts.identity import IdentityRepositoriesContract
from tests.contracts.job_queue import JobQueueContract
from tests.contracts.rules import RuleRepositoriesContract
from tests.contracts.unit_of_work import UnitOfWorkContract, owner_with_drawer
from tests.contracts.webhooks import WebhookRepositoryContract
from tests.sql_suite import MigrationSuite, SqlAdapterSuite


class TestSqliteUnitOfWork(UnitOfWorkContract):
    pass


class TestSqliteJobQueue(JobQueueContract):
    pass


class TestSqliteRuleRepositories(RuleRepositoriesContract):
    pass


class TestSqliteWebhookRepository(WebhookRepositoryContract):
    pass


class TestSqliteIdentityRepositories(IdentityRepositoriesContract):
    pass


class TestSqliteEventBus(EventBusContract):
    pass


class TestSqliteAdapter(SqlAdapterSuite):
    pass


class TestSqliteMigrations(MigrationSuite):
    pass


async def test_connections_use_wal_and_foreign_keys(database: Database) -> None:
    async with database.reading() as connection:
        assert (await connection.exec_driver_sql("PRAGMA journal_mode")).scalar() == "wal"
        assert (await connection.exec_driver_sql("PRAGMA foreign_keys")).scalar() == 1
        assert (await connection.exec_driver_sql("PRAGMA busy_timeout")).scalar() == 15000


async def test_decimals_and_timestamps_are_stored_as_exact_text(
    database: Database, uow_factory: UnitOfWorkFactory
) -> None:
    """SQLite has no decimal or timezone types: decimals are their exact text, timestamps UTC
    text of fixed width (so text order is time order)."""
    owner, drawer = await owner_with_drawer(uow_factory)
    amount = FieldDefinition.create(name="a", data_type=FieldType.AMOUNT, now=NOW)
    document = builders.document(owner, drawer)
    document.apply_changes(
        DocumentChanges(fields={amount.id: Money(Decimal("-12.30"), "CHF")}),
        {amount.id: amount},
        NOW,
    )
    async with uow_factory() as uow:
        await uow.fields.add(amount)
        await uow.documents.add(document)
        await uow.commit()
    async with database.reading() as connection:
        value = await connection.execute(
            text("SELECT value_decimal, typeof(value_decimal) FROM document_fields")
        )
        created = await connection.execute(text("SELECT created_at FROM documents"))
        assert tuple(value.one()) == ("-12.30", "text")
        assert created.scalar() == "2026-10-06 12:00:00.000000"


async def test_a_second_writing_unit_in_the_same_task_fails_at_once(
    uow_factory: UnitOfWorkFactory,
) -> None:
    """It would wait for its own write lock until the busy timeout."""
    async with uow_factory() as outer:
        await outer.users.add(builders.user("outer"))
        async with uow_factory() as inner:
            await inner.users.find_by_username("outer")  # reading is fine
            with pytest.raises(RuntimeError, match="already has an open write transaction"):
                await inner.users.add(builders.user("inner"))
        await outer.commit()

    async with uow_factory() as after:  # the lock was released with the commit
        await after.users.add(builders.user("after"))
        await after.commit()


async def test_other_tasks_wait_for_the_writer(uow_factory: UnitOfWorkFactory) -> None:
    order: list[str] = []

    async def write(name: str, hold: float) -> None:
        async with uow_factory() as uow:
            await uow.users.add(builders.user(name))
            order.append(f"{name} writes")
            await asyncio.sleep(hold)
            await uow.commit()
            order.append(f"{name} committed")

    first = asyncio.create_task(write("first", 0.2))
    await asyncio.sleep(0.05)
    await write("second", 0)
    await first
    assert order == ["first writes", "first committed", "second writes", "second committed"]


async def test_a_failed_unit_releases_the_task(uow_factory: UnitOfWorkFactory) -> None:
    with pytest.raises(ValueError, match="boom"):
        async with uow_factory() as uow:
            await uow.users.add(builders.user("a"))
            raise ValueError("boom")
    async with uow_factory() as uow:
        await uow.users.add(builders.user("b"))
        await uow.commit()
