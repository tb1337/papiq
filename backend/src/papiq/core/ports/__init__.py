"""Outbound ports: interfaces the core needs, implemented by outbound adapters."""

from papiq.core.ports.clock import Clock
from papiq.core.ports.embeddings import Embeddings
from papiq.core.ports.event_bus import DeliveryRetry, EventBus, EventHandler, Outbox
from papiq.core.ports.identity import IdentityProvider
from papiq.core.ports.job_queue import JobQueue
from papiq.core.ports.llm import LanguageModel
from papiq.core.ports.object_store import ObjectStore
from papiq.core.ports.ocr import Ocr
from papiq.core.ports.parser import DocumentParser
from papiq.core.ports.repository import (
    AttributeDefinitionRepository,
    ContactRepository,
    DocumentRepository,
    DocumentTypeRepository,
    DrawerRepository,
    NamedRepository,
    ProcessingLog,
    Repository,
    TagRepository,
    UserRepository,
)
from papiq.core.ports.search_index import SearchIndex
from papiq.core.ports.unit_of_work import UnitOfWork, UnitOfWorkFactory

__all__ = [
    "AttributeDefinitionRepository",
    "Clock",
    "ContactRepository",
    "DeliveryRetry",
    "DocumentParser",
    "DocumentRepository",
    "DocumentTypeRepository",
    "DrawerRepository",
    "Embeddings",
    "EventBus",
    "EventHandler",
    "IdentityProvider",
    "JobQueue",
    "LanguageModel",
    "NamedRepository",
    "ObjectStore",
    "Ocr",
    "Outbox",
    "ProcessingLog",
    "Repository",
    "SearchIndex",
    "TagRepository",
    "UnitOfWork",
    "UnitOfWorkFactory",
    "UserRepository",
]
