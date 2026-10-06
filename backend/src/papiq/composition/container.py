"""Composition root: choose the configured adapter for each port.

Adapters register their factories in the tables below, keyed by the configured adapter name.
No adapter exists yet, so every selection fails with AdapterNotAvailableError.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from papiq.composition.errors import AdapterNotAvailableError
from papiq.composition.settings import Settings
from papiq.core.ports import (
    DocumentParser,
    Embeddings,
    EventBus,
    IdentityProvider,
    JobQueue,
    LanguageModel,
    ObjectStore,
    Ocr,
    Repository,
    SearchIndex,
)

type Factory[T] = Callable[[Settings], T]

# Keys: `PAPIQ_DB_TYPE` (repository, job queue, event bus), `PAPIQ_STORAGE_TYPE` (object store),
# or the fixed adapter name for ports with a single adapter.
REPOSITORIES: dict[str, Factory[Repository]] = {}
JOB_QUEUES: dict[str, Factory[JobQueue]] = {}
EVENT_BUSES: dict[str, Factory[EventBus]] = {}
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
    milestones that need them (M6, M5)."""

    repository: Repository
    job_queue: JobQueue
    event_bus: EventBus
    object_store: ObjectStore
    ocr: Ocr
    parser: DocumentParser
    identity: IdentityProvider
    search_index: SearchIndex | None
    language_model: LanguageModel | None
    embeddings: Embeddings | None


def build_container(settings: Settings) -> Container:
    """Create the adapter for every port. Raises AdapterNotAvailableError for a missing one."""
    return Container(
        repository=_select("repository", settings.db_type, REPOSITORIES, settings),
        job_queue=_select("job_queue", settings.db_type, JOB_QUEUES, settings),
        event_bus=_select("event_bus", settings.db_type, EVENT_BUSES, settings),
        object_store=_select("object_store", settings.storage_type, OBJECT_STORES, settings),
        ocr=_select("ocr", "ocrmypdf", OCR_ENGINES, settings),
        parser=_select("parser", "docling", PARSERS, settings),
        identity=_select("identity", "native", IDENTITY_PROVIDERS, settings),
        search_index=(
            _select("search_index", "meilisearch", SEARCH_INDEXES, settings)
            if settings.meilisearch_url
            else None
        ),
        language_model=(
            _select("llm", "openai-compatible", LANGUAGE_MODELS, settings)
            if settings.llm_base_url
            else None
        ),
        embeddings=(
            _select("embeddings", "openai-compatible", EMBEDDINGS, settings)
            if settings.embedding_base_url
            else None
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
