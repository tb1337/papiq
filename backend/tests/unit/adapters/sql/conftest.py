import shutil
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from papiq.adapters.outbound.sql import Database, SqlEventBus, SqlUnitOfWorkFactory, migrate
from papiq.core.ports import Clock, DeliveryRetry, EventBus, UnitOfWorkFactory
from papiq.core.ports.event_bus import DEFAULT_DELIVERY_RETRY
from tests.contracts.event_bus import EventBusFactory


@pytest.fixture(scope="session")
def sqlite_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A database file migrated once with the real migrations; tests work on copies."""
    import asyncio

    path = tmp_path_factory.mktemp("sqlite") / "template.db"

    async def create() -> None:
        database = Database.sqlite(path)
        try:
            await migrate(database)
        finally:
            await database.dispose()

    asyncio.run(create())
    return path


@pytest.fixture
async def database(sqlite_template: Path, tmp_path: Path) -> AsyncIterator[Database]:
    """A fresh SQLite file in the test's directory (not `:memory:`: units of work need
    connections of their own)."""
    path = tmp_path / "papiq.db"
    shutil.copyfile(sqlite_template, path)
    database = Database.sqlite(path)
    yield database
    await database.dispose()


@pytest.fixture
def uow_factory(database: Database) -> UnitOfWorkFactory:
    return SqlUnitOfWorkFactory(database)


@pytest.fixture
def event_bus_factory(database: Database) -> EventBusFactory:
    def create(
        *, clock: Clock | None = None, retry: DeliveryRetry = DEFAULT_DELIVERY_RETRY
    ) -> EventBus:
        return SqlEventBus(database, clock=clock, retry=retry)

    return create


@pytest.fixture
async def empty_database(tmp_path: Path) -> AsyncIterator[Database]:
    database = Database.sqlite(tmp_path / "empty.db")
    yield database
    await database.dispose()


@pytest.fixture
async def model_database(tmp_path: Path) -> AsyncIterator[Database]:
    database = Database.sqlite(tmp_path / "model.db")
    yield database
    await database.dispose()
