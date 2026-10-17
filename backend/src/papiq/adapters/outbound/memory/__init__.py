"""In-memory adapters for every port, used in core tests and local development.

They behave like the real adapters, including transactions: nothing is stored without commit.
"""

from papiq.adapters.outbound.memory.classifier import FakeClassifier
from papiq.adapters.outbound.memory.clock import ManualClock
from papiq.adapters.outbound.memory.crypto import FakeCipher, FakePasswordHasher, FakeTotp
from papiq.adapters.outbound.memory.database import MemoryDatabase
from papiq.adapters.outbound.memory.event_bus import MemoryEventBus
from papiq.adapters.outbound.memory.images import FAKE_LOGO, FakeImageNormaliser
from papiq.adapters.outbound.memory.language_model import (
    BagOfWordsEmbeddings,
    FakeEmbeddings,
    FakeLanguageModel,
)
from papiq.adapters.outbound.memory.logos import FakeLogoFetcher
from papiq.adapters.outbound.memory.object_store import MemoryObjectStore
from papiq.adapters.outbound.memory.oidc import FakeOidcProvider
from papiq.adapters.outbound.memory.processing import (
    FAKE_GIRO_CODE,
    FAKE_VISION,
    FakeCodeReader,
    FakeMetadataReader,
    FakeOcr,
    FakePageRenderer,
    FakeParser,
    FakeTextLocator,
)
from papiq.adapters.outbound.memory.search_index import MemorySearchIndex
from papiq.adapters.outbound.memory.unit_of_work import MemoryUnitOfWork, MemoryUnitOfWorkFactory
from papiq.adapters.outbound.memory.webhook_sender import FakeWebhookSender

__all__ = [
    "FAKE_GIRO_CODE",
    "FAKE_LOGO",
    "FAKE_VISION",
    "BagOfWordsEmbeddings",
    "FakeCipher",
    "FakeClassifier",
    "FakeCodeReader",
    "FakeEmbeddings",
    "FakeImageNormaliser",
    "FakeLanguageModel",
    "FakeLogoFetcher",
    "FakeMetadataReader",
    "FakeOcr",
    "FakeOidcProvider",
    "FakePageRenderer",
    "FakeParser",
    "FakePasswordHasher",
    "FakeTextLocator",
    "FakeTotp",
    "FakeWebhookSender",
    "ManualClock",
    "MemoryDatabase",
    "MemoryEventBus",
    "MemoryObjectStore",
    "MemorySearchIndex",
    "MemoryUnitOfWork",
    "MemoryUnitOfWorkFactory",
]
