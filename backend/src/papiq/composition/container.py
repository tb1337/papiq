"""Composition root: choose the configured adapter for each port.

Adapters register their factories in the tables below, keyed by the configured adapter name.
No production adapter exists yet, so every selection fails with AdapterNotAvailableError.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from papiq import __version__
from papiq.adapters.outbound.memory import (
    MemoryDatabase,
    MemoryEventBus,
    MemoryObjectStore,
    MemoryUnitOfWorkFactory,
)
from papiq.adapters.outbound.system import SystemClock
from papiq.composition.errors import AdapterNotAvailableError
from papiq.composition.settings import Settings
from papiq.core.domain.pipeline import PIPELINE
from papiq.core.ports import (
    Clock,
    DocumentParser,
    Embeddings,
    EventBus,
    IdentityProvider,
    LanguageModel,
    ObjectStore,
    Ocr,
    SearchIndex,
    UnitOfWorkFactory,
)
from papiq.core.services.documents import DocumentService
from papiq.core.services.drawers import DrawerService
from papiq.core.services.master_data import MasterDataService
from papiq.core.services.pipeline import PipelineService, PlaceholderStep
from papiq.core.services.users import UserService

type Factory[T] = Callable[[Settings], T]


@dataclass(frozen=True)
class Persistence:
    """The database-backed ports. They share one database: repositories, processing log,
    outbox and job queue through the unit of work, and the event bus reading the outbox."""

    unit_of_work: UnitOfWorkFactory
    event_bus: EventBus


# Keys: `PAPIQ_DB_TYPE` (persistence), `PAPIQ_STORAGE_TYPE` (object store),
# or the fixed adapter name for ports with a single adapter.
PERSISTENCE: dict[str, Factory[Persistence]] = {}
OBJECT_STORES: dict[str, Factory[ObjectStore]] = {}
SEARCH_INDEXES: dict[str, Factory[SearchIndex]] = {}
LANGUAGE_MODELS: dict[str, Factory[LanguageModel]] = {}
EMBEDDINGS: dict[str, Factory[Embeddings]] = {}
OCR_ENGINES: dict[str, Factory[Ocr]] = {}
PARSERS: dict[str, Factory[DocumentParser]] = {}
IDENTITY_PROVIDERS: dict[str, Factory[IdentityProvider]] = {}


@dataclass(frozen=True)
class Container:
    """One adapter per port. Search, language model and embeddings are optional until the
    milestones that need them (M6, M5); OCR, parser and identity are missing in the in-memory
    container until their ports are designed (M3, M4)."""

    unit_of_work: UnitOfWorkFactory
    event_bus: EventBus
    object_store: ObjectStore
    clock: Clock
    ocr: Ocr | None
    parser: DocumentParser | None
    identity: IdentityProvider | None
    search_index: SearchIndex | None
    language_model: LanguageModel | None
    embeddings: Embeddings | None


def build_container(settings: Settings) -> Container:
    """Create the adapter for every port. Raises AdapterNotAvailableError for a missing one."""
    persistence = _select("persistence", settings.db_type, PERSISTENCE, settings)
    return Container(
        unit_of_work=persistence.unit_of_work,
        event_bus=persistence.event_bus,
        clock=SystemClock(),
        object_store=_select("object_store", settings.storage_type, OBJECT_STORES, settings),
        ocr=_select("ocr", "ocrmypdf", OCR_ENGINES, settings),
        parser=_select("parser", "docling", PARSERS, settings),
        identity=_select("identity", "native", IDENTITY_PROVIDERS, settings),
        search_index=(
            _select("search_index", "meilisearch", SEARCH_INDEXES, settings)
            if settings.meilisearch_url is not None
            else None
        ),
        language_model=(
            _select("llm", "openai-compatible", LANGUAGE_MODELS, settings)
            if settings.llm_base_url is not None
            else None
        ),
        embeddings=(
            _select("embeddings", "openai-compatible", EMBEDDINGS, settings)
            if settings.embedding_base_url is not None
            else None
        ),
    )


def build_memory_container(clock: Clock | None = None) -> Container:
    """All ports on in-memory adapters, for tests and local experiments. Nothing persists."""
    database = MemoryDatabase()
    return Container(
        unit_of_work=MemoryUnitOfWorkFactory(database),
        event_bus=MemoryEventBus(database),
        object_store=MemoryObjectStore(),
        clock=clock or SystemClock(),
        ocr=None,
        parser=None,
        identity=None,
        search_index=None,
        language_model=None,
        embeddings=None,
    )


@dataclass(frozen=True)
class Services:
    """The use cases, wired to the container's adapters."""

    users: UserService
    drawers: DrawerService
    master_data: MasterDataService
    documents: DocumentService
    pipeline: PipelineService


def build_services(container: Container) -> Services:
    """Pipeline steps after receive are placeholders until M3 (OCR, parsing), M5
    (classification, attributes) and M7 (rules)."""
    uow, clock = container.unit_of_work, container.clock
    return Services(
        users=UserService(uow, clock),
        drawers=DrawerService(uow, clock),
        master_data=MasterDataService(uow, clock),
        documents=DocumentService(uow, clock),
        pipeline=PipelineService(
            uow,
            clock,
            container.object_store,
            {step: PlaceholderStep() for step in PIPELINE[1:]},
            pipeline_version=__version__,
        ),
    )


def _select[T](
    port: str, adapter: str, factories: Mapping[str, Factory[T]], settings: Settings
) -> T:
    try:
        factory = factories[adapter]
    except KeyError:
        raise AdapterNotAvailableError(port, adapter) from None
    return factory(settings)
