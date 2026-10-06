"""Postgres for the SQL adapter tests: a database of its own per test session, migrated once
with the real migrations and emptied before every test. Skips if Postgres is not configured or
not reachable; the CI checks that the Postgres run did not skip."""

import asyncio
import secrets
from collections.abc import AsyncIterator, Callable, Iterator

import pytest
from sqlalchemy import text

from papiq.adapters.outbound.sql import Database, SqlEventBus, SqlUnitOfWorkFactory, migrate
from papiq.adapters.outbound.sql.tables import metadata
from papiq.composition.settings import Settings
from papiq.core.ports import EventBus, UnitOfWorkFactory
from tests import probes


def postgres(settings: Settings, name: str | None = None) -> Database:
    assert settings.db_host and settings.db_name and settings.db_user and settings.db_password
    return Database.postgres(
        host=settings.db_host,
        port=settings.db_port,
        name=name or settings.db_name,
        user=settings.db_user,
        password=settings.db_password.get_secret_value(),
    )


async def create_database(settings: Settings, name: str) -> None:
    admin = postgres(settings)
    try:
        async with admin.engine.connect() as connection:
            connection = await connection.execution_options(isolation_level="AUTOCOMMIT")
            await connection.exec_driver_sql(f'CREATE DATABASE "{name}"')
    finally:
        await admin.dispose()


async def drop_database(settings: Settings, name: str) -> None:
    admin = postgres(settings)
    try:
        async with admin.engine.connect() as connection:
            connection = await connection.execution_options(isolation_level="AUTOCOMMIT")
            await connection.exec_driver_sql(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await admin.dispose()


@pytest.fixture(scope="session")
def postgres_settings(settings: Settings) -> Settings:
    if settings.db_type != "postgres" or settings.db_host is None:
        pytest.skip("PAPIQ_DB_TYPE is not postgres")
    if not probes.postgres_answers(settings.db_host, settings.db_port):
        pytest.skip(f"Postgres not reachable at {settings.db_host}:{settings.db_port}")
    return settings


@pytest.fixture
def new_database_name(postgres_settings: Settings) -> Iterator[str]:
    """The name of a database that does not exist yet; dropped after the test."""
    name = f"{postgres_settings.db_name}_test_{secrets.token_hex(4)}"
    yield name
    asyncio.run(drop_database(postgres_settings, name))


@pytest.fixture(scope="session")
def test_database_name(postgres_settings: Settings) -> Iterator[str]:
    name = f"{postgres_settings.db_name}_test_{secrets.token_hex(4)}"

    async def create() -> None:
        await create_database(postgres_settings, name)
        database = postgres(postgres_settings, name)
        try:
            await migrate(database)
        finally:
            await database.dispose()

    asyncio.run(create())
    yield name
    asyncio.run(drop_database(postgres_settings, name))


@pytest.fixture
async def database(postgres_settings: Settings, test_database_name: str) -> AsyncIterator[Database]:
    database = postgres(postgres_settings, test_database_name)
    names = ", ".join(table.name for table in metadata.sorted_tables)
    async with database.engine.begin() as connection:
        await connection.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
    yield database
    await database.dispose()


@pytest.fixture
def uow_factory(database: Database) -> UnitOfWorkFactory:
    return SqlUnitOfWorkFactory(database)


@pytest.fixture
def event_bus_factory(database: Database) -> Callable[[], EventBus]:
    return lambda: SqlEventBus(database)


@pytest.fixture
async def empty_database(
    postgres_settings: Settings, new_database_name: str
) -> AsyncIterator[Database]:
    await create_database(postgres_settings, new_database_name)
    database = postgres(postgres_settings, new_database_name)
    yield database
    await database.dispose()
