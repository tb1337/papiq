"""The in-memory adapters pass every contract suite."""

import pytest

from papiq.adapters.outbound.memory import (
    ManualClock,
    MemoryDatabase,
    MemoryEventBus,
    MemoryObjectStore,
    MemoryUnitOfWorkFactory,
)
from papiq.core.ports import Clock, DeliveryRetry, EventBus, ObjectStore, UnitOfWorkFactory
from papiq.core.ports.event_bus import DEFAULT_DELIVERY_RETRY
from tests.contracts.clock import ClockContract
from tests.contracts.event_bus import EventBusContract, EventBusFactory
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
def event_bus_factory(database: MemoryDatabase) -> EventBusFactory:
    def create(
        *, clock: Clock | None = None, retry: DeliveryRetry = DEFAULT_DELIVERY_RETRY
    ) -> EventBus:
        return MemoryEventBus(database, clock=clock, retry=retry)

    return create


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
