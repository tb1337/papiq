"""In-memory adapters for every port, used in core tests and local development.

They behave like the real adapters, including transactions: nothing is stored without commit.
"""

from papiq.adapters.outbound.memory.clock import ManualClock
from papiq.adapters.outbound.memory.database import MemoryDatabase
from papiq.adapters.outbound.memory.event_bus import MemoryEventBus
from papiq.adapters.outbound.memory.object_store import MemoryObjectStore
from papiq.adapters.outbound.memory.processing import FakeOcr, FakeParser, FakePreviewRenderer
from papiq.adapters.outbound.memory.unit_of_work import MemoryUnitOfWork, MemoryUnitOfWorkFactory

__all__ = [
    "FakeOcr",
    "FakeParser",
    "FakePreviewRenderer",
    "ManualClock",
    "MemoryDatabase",
    "MemoryEventBus",
    "MemoryObjectStore",
    "MemoryUnitOfWork",
    "MemoryUnitOfWorkFactory",
]
