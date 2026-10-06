"""The in-memory adapters pass every contract suite."""

import pytest

from papiq.adapters.outbound.memory import (
    ManualClock,
    MemoryDatabase,
    MemoryEventBus,
    MemoryObjectStore,
    MemoryUnitOfWorkFactory,
)
from papiq.core.ports import Clock, EventBus, ObjectStore, UnitOfWorkFactory
from tests.contracts.clock import ClockContract
from tests.contracts.event_bus import EventBusContract
from tests.contracts.job_queue import JobQueueContract
from tests.contracts.object_store import ObjectStoreContract
from tests.contracts.unit_of_work import UnitOfWorkContract


@pytest.fixture
def database() -> MemoryDatabase:
    return MemoryDatabase()


@pytest.fixture
def uow_factory(database: MemoryDatabase) -> UnitOfWorkFactory:
    return MemoryUnitOfWorkFactory(database)


@pytest.fixture
def event_bus(database: MemoryDatabase) -> EventBus:
    return MemoryEventBus(database)


@pytest.fixture
def object_store() -> ObjectStore:
    return MemoryObjectStore()


@pytest.fixture
def clock() -> Clock:
    return ManualClock()


class TestMemoryUnitOfWork(UnitOfWorkContract):
    pass


class TestMemoryJobQueue(JobQueueContract):
    pass


class TestMemoryEventBus(EventBusContract):
    pass


class TestMemoryObjectStore(ObjectStoreContract):
    pass


class TestManualClock(ClockContract):
    pass
