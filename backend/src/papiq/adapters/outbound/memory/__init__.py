"""In-memory adapters for every port, used in core tests and local development.

They behave like the real adapters, including transactions: nothing is stored without commit.
"""

from papiq.adapters.outbound.memory.clock import ManualClock
from papiq.adapters.outbound.memory.crypto import FakeCipher, FakePasswordHasher, FakeTotp
from papiq.adapters.outbound.memory.database import MemoryDatabase
from papiq.adapters.outbound.memory.event_bus import MemoryEventBus
from papiq.adapters.outbound.memory.language_model import FakeEmbeddings, FakeLanguageModel
from papiq.adapters.outbound.memory.object_store import MemoryObjectStore
from papiq.adapters.outbound.memory.oidc import FakeOidcProvider
from papiq.adapters.outbound.memory.processing import FakeOcr, FakeParser, FakePreviewRenderer
from papiq.adapters.outbound.memory.unit_of_work import MemoryUnitOfWork, MemoryUnitOfWorkFactory

__all__ = [
    "FakeCipher",
    "FakeEmbeddings",
    "FakeLanguageModel",
    "FakeOcr",
    "FakeOidcProvider",
    "FakeParser",
    "FakePasswordHasher",
    "FakePreviewRenderer",
    "FakeTotp",
    "ManualClock",
    "MemoryDatabase",
    "MemoryEventBus",
    "MemoryObjectStore",
    "MemoryUnitOfWork",
    "MemoryUnitOfWorkFactory",
]
