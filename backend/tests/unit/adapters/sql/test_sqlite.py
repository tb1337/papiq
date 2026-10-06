"""The SQL adapters on SQLite pass every contract suite and the SQL suites."""

from decimal import Decimal

from sqlalchemy import text

from papiq.adapters.outbound.sql import Database
from papiq.core.domain.attributes import AttributeDefinition, AttributeType, Money
from papiq.core.domain.documents import DocumentChanges
from papiq.core.ports import UnitOfWorkFactory
from tests import builders
from tests.builders import NOW
from tests.contracts.event_bus import EventBusContract
from tests.contracts.job_queue import JobQueueContract
from tests.contracts.unit_of_work import UnitOfWorkContract, owner_with_drawer
from tests.sql_suite import MigrationSuite, SqlAdapterSuite


class TestSqliteUnitOfWork(UnitOfWorkContract):
    pass


class TestSqliteJobQueue(JobQueueContract):
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
    amount = AttributeDefinition.create(name="a", data_type=AttributeType.AMOUNT, now=NOW)
    document = builders.document(owner, drawer)
    document.apply_changes(
        DocumentChanges(attributes={amount.id: Money(Decimal("-12.30"), "CHF")}),
        {amount.id: amount},
        NOW,
    )
    async with uow_factory() as uow:
        await uow.attributes.add(amount)
        await uow.documents.add(document)
        await uow.commit()
    async with database.reading() as connection:
        value = await connection.execute(
            text("SELECT value_decimal, typeof(value_decimal) FROM document_attributes")
        )
        created = await connection.execute(text("SELECT created_at FROM documents"))
        assert tuple(value.one()) == ("-12.30", "text")
        assert created.scalar() == "2026-10-06 12:00:00.000000"
