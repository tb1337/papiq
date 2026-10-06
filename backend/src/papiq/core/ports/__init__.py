"""Outbound ports: interfaces the core needs, implemented by outbound adapters."""

from papiq.core.ports.embeddings import Embeddings
from papiq.core.ports.event_bus import EventBus
from papiq.core.ports.identity import IdentityProvider
from papiq.core.ports.job_queue import JobQueue
from papiq.core.ports.llm import LanguageModel
from papiq.core.ports.object_store import ObjectStore
from papiq.core.ports.ocr import Ocr
from papiq.core.ports.parser import DocumentParser
from papiq.core.ports.repository import Repository
from papiq.core.ports.search_index import SearchIndex

__all__ = [
    "DocumentParser",
    "Embeddings",
    "EventBus",
    "IdentityProvider",
    "JobQueue",
    "LanguageModel",
    "ObjectStore",
    "Ocr",
    "Repository",
    "SearchIndex",
]
