"""Composition root: choose the configured adapter for each port.

Adapters register their factories in the tables below, keyed by the configured adapter name.
A port whose configured adapter does not exist yet fails with AdapterNotAvailableError.
"""

import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import timedelta

from pydantic import SecretStr

from papiq import __version__
from papiq.adapters.inbound.rest import PREFIX
from papiq.adapters.outbound.crypto import (
    AesGcmCipher,
    Argon2PasswordHasher,
    PyotpTotp,
    decode_key,
)
from papiq.adapters.outbound.docling import DoclingParser
from papiq.adapters.outbound.filesystem import FilesystemObjectStore
from papiq.adapters.outbound.meilisearch import MeilisearchIndex
from papiq.adapters.outbound.memory import (
    FakeCipher,
    FakeOcr,
    FakeParser,
    FakePasswordHasher,
    FakePreviewRenderer,
    FakeTotp,
    MemoryDatabase,
    MemoryEventBus,
    MemoryObjectStore,
    MemorySearchIndex,
    MemoryUnitOfWorkFactory,
)
from papiq.adapters.outbound.ocrmypdf import OcrmypdfEngine
from papiq.adapters.outbound.oidc import AuthlibOidcProvider
from papiq.adapters.outbound.openai_compat import OpenAiCompatEmbeddings, OpenAiCompatLanguageModel
from papiq.adapters.outbound.pdfium import PdfiumPreviewRenderer
from papiq.adapters.outbound.s3 import S3ObjectStore
from papiq.adapters.outbound.sql import SqlEventBus, SqlUnitOfWorkFactory
from papiq.adapters.outbound.system import SystemClock
from papiq.composition.database import open_database
from papiq.composition.errors import AdapterNotAvailableError
from papiq.composition.settings import Settings
from papiq.core.domain.pipeline import PIPELINE, Step
from papiq.core.ports import (
    Clock,
    DecryptionError,
    DeliveryRetry,
    DocumentParser,
    Embeddings,
    EventBus,
    LanguageModel,
    ObjectStore,
    Ocr,
    OidcProvider,
    PasswordHasher,
    PreviewRenderer,
    SearchIndex,
    SecretCipher,
    Totp,
    UnitOfWorkFactory,
)
from papiq.core.services.auth import AuthService, SessionPolicy
from papiq.core.services.classification.steps import (
    ClassificationPolicy,
    ClassifyStep,
    ExtractAttributesStep,
)
from papiq.core.services.documents import DocumentService
from papiq.core.services.drawers import DrawerService
from papiq.core.services.indexing import IndexingPolicy, IndexingService
from papiq.core.services.maintenance import MaintenanceService
from papiq.core.services.master_data import MasterDataService
from papiq.core.services.oidc import OidcService
from papiq.core.services.pipeline import PipelineService, PlaceholderStep, RetryPolicy, StepExecutor
from papiq.core.services.search import SearchPolicy, SearchService
from papiq.core.services.steps import OcrStep, ParseStep
from papiq.core.services.users import UserService

type Factory[T] = Callable[[Settings], T]
type Closer = Callable[[], Awaitable[None]]


@dataclass(frozen=True)
class Persistence:
    """The database-backed ports. They share one database: repositories, processing log,
    outbox and job queue through the unit of work, and the event bus reading the outbox."""

    unit_of_work: UnitOfWorkFactory
    event_bus: EventBus
    close: Closer


def sql_persistence(settings: Settings) -> Persistence:
    """SQLite or Postgres, per `PAPIQ_DB_TYPE`; the schema must be migrated."""
    database = open_database(settings)
    retry = DeliveryRetry(max_attempts=settings.events_max_attempts)
    return Persistence(
        unit_of_work=SqlUnitOfWorkFactory(database),
        event_bus=SqlEventBus(database, retry=retry),
        close=database.dispose,
    )


def filesystem_store(settings: Settings) -> FilesystemObjectStore:
    return FilesystemObjectStore(settings.storage_path)


def s3_store(settings: Settings) -> S3ObjectStore:
    # Settings guarantee these for s3.
    assert settings.s3_endpoint_url and settings.s3_bucket
    assert settings.s3_access_key_id and settings.s3_secret_access_key
    return S3ObjectStore(
        endpoint_url=str(settings.s3_endpoint_url),
        region=settings.s3_region,
        bucket=settings.s3_bucket,
        access_key_id=settings.s3_access_key_id.get_secret_value(),
        secret_access_key=settings.s3_secret_access_key.get_secret_value(),
        path_style=settings.s3_path_style,
    )


# Keys: `PAPIQ_DB_TYPE` (persistence), `PAPIQ_STORAGE_TYPE` (object store),
# or the fixed adapter name for ports with a single adapter.
PERSISTENCE: dict[str, Factory[Persistence]] = {
    "sqlite": sql_persistence,
    "postgres": sql_persistence,
}
OBJECT_STORES: dict[str, Factory[ObjectStore]] = {
    "filesystem": filesystem_store,
    "s3": s3_store,
}


def openai_language_model(settings: Settings) -> OpenAiCompatLanguageModel:
    # Settings guarantee these together.
    assert settings.llm_base_url is not None and settings.llm_model is not None
    return OpenAiCompatLanguageModel(
        base_url=str(settings.llm_base_url),
        model=settings.llm_model,
        api_key=_secret(settings.llm_api_key),
        temperature=settings.llm_temperature,
        seed=settings.llm_seed,
        timeout=settings.llm_timeout.total_seconds(),
        response_format=settings.llm_response_format,
    )


def openai_embeddings(settings: Settings) -> OpenAiCompatEmbeddings:
    assert settings.embedding_base_url is not None and settings.embedding_model is not None
    return OpenAiCompatEmbeddings(
        base_url=str(settings.embedding_base_url),
        model=settings.embedding_model,
        api_key=_secret(settings.embedding_api_key),
        timeout=settings.embedding_timeout.total_seconds(),
    )


def _secret(value: SecretStr | None) -> str | None:
    return None if value is None else value.get_secret_value()


def meilisearch_index(settings: Settings) -> MeilisearchIndex:
    assert settings.meilisearch_url is not None  # the adapter is only selected with a URL
    return MeilisearchIndex(
        url=str(settings.meilisearch_url),
        api_key=_secret(settings.meilisearch_api_key),
        index=settings.meilisearch_index,
        # Vectors only where there is a model to compute them (settings guarantee the length).
        dimensions=settings.embedding_dimensions if settings.embedding_base_url else None,
        locales=settings.search_locales.split("+"),
        timeout=settings.meilisearch_timeout.total_seconds(),
        task_timeout=settings.meilisearch_task_timeout.total_seconds(),
    )


SEARCH_INDEXES: dict[str, Factory[SearchIndex]] = {"meilisearch": meilisearch_index}
LANGUAGE_MODELS: dict[str, Factory[LanguageModel]] = {"openai-compatible": openai_language_model}
EMBEDDINGS: dict[str, Factory[Embeddings]] = {"openai-compatible": openai_embeddings}


def _cores_per_job(settings: Settings) -> int:
    """The cores are shared by the jobs that run at the same time."""
    return max(1, (os.cpu_count() or 1) // settings.worker_concurrency)


def ocrmypdf_engine(settings: Settings) -> OcrmypdfEngine:
    return OcrmypdfEngine(
        languages=settings.ocr_languages.split("+"),
        timeout=settings.ocr_timeout,
        jobs=_cores_per_job(settings),
    )


def docling_parser(settings: Settings) -> DoclingParser:
    return DoclingParser(
        models=settings.docling_models_path,
        timeout=settings.parse_timeout,
        threads=_cores_per_job(settings),
    )


OCR_ENGINES: dict[str, Factory[Ocr]] = {"ocrmypdf": ocrmypdf_engine}
PARSERS: dict[str, Factory[DocumentParser]] = {"docling": docling_parser}
PREVIEW_RENDERERS: dict[str, Factory[PreviewRenderer]] = {
    "pdfium": lambda _: PdfiumPreviewRenderer()
}


class MissingKeyCipher:
    """Stands in where `PAPIQ_SECRET_KEY` is not set (the worker): any use is an error."""

    def encrypt(self, plaintext: bytes, *, context: bytes) -> bytes:
        raise RuntimeError("PAPIQ_SECRET_KEY is not set")

    def decrypt(self, ciphertext: bytes, *, context: bytes) -> bytes:
        raise DecryptionError("PAPIQ_SECRET_KEY is not set")


def redirect_uri(settings: Settings) -> str:
    """Where the provider sends the browser back: the API's OIDC callback."""
    assert settings.public_url is not None
    return str(settings.public_url).rstrip("/") + PREFIX + "/auth/oidc/callback"


def oidc_provider(settings: Settings) -> OidcProvider:
    # Settings guarantee these with OIDC.
    assert settings.oidc_issuer and settings.oidc_client_id and settings.oidc_client_secret
    return AuthlibOidcProvider(
        issuer=settings.oidc_issuer,
        client_id=settings.oidc_client_id,
        client_secret=settings.oidc_client_secret.get_secret_value(),
        redirect_uri=redirect_uri(settings),
        scopes=settings.oidc_scopes.split(),
        username_claim=settings.oidc_username_claim,
    )


OIDC_PROVIDERS: dict[str, Factory[OidcProvider]] = {"authlib": oidc_provider}


def secret_cipher(settings: Settings) -> SecretCipher:
    if settings.secret_key is None:
        return MissingKeyCipher()
    return AesGcmCipher(decode_key(settings.secret_key.get_secret_value()))


@dataclass(frozen=True)
class Container:
    """One adapter per port. Search, language model and embeddings are optional until the
    milestones that need them (M6, M5).

    `aclose` releases what the adapters hold (database engine, S3 client); call it when the
    API or the worker stops.
    """

    unit_of_work: UnitOfWorkFactory
    event_bus: EventBus
    object_store: ObjectStore
    clock: Clock
    ocr: Ocr
    parser: DocumentParser
    previews: PreviewRenderer
    password_hasher: PasswordHasher
    cipher: SecretCipher
    totp: Totp
    oidc: OidcProvider | None
    search_index: SearchIndex | None
    language_model: LanguageModel | None
    embeddings: Embeddings | None
    closers: tuple[Closer, ...] = field(default=(), repr=False)

    async def aclose(self) -> None:
        """Close in reverse order of creation; every closer runs even if one fails."""
        errors: list[Exception] = []
        for close in reversed(self.closers):
            try:
                await close()
            except Exception as error:
                errors.append(error)
        if errors:
            raise ExceptionGroup("closing the container failed", errors)


def build_container(settings: Settings) -> Container:
    """Create the adapter for every port. Raises AdapterNotAvailableError for a missing one."""
    persistence = _select("persistence", settings.db_type, PERSISTENCE, settings)
    closers: list[Closer] = [persistence.close]
    object_store = _select("object_store", settings.storage_type, OBJECT_STORES, settings)
    if isinstance(object_store, S3ObjectStore):
        closers.append(object_store.aclose)
    search_index = (
        _select("search_index", "meilisearch", SEARCH_INDEXES, settings)
        if settings.meilisearch_url is not None
        else None
    )
    if isinstance(search_index, MeilisearchIndex):
        closers.append(search_index.aclose)
    return Container(
        unit_of_work=persistence.unit_of_work,
        event_bus=persistence.event_bus,
        clock=SystemClock(),
        object_store=object_store,
        ocr=_select("ocr", "ocrmypdf", OCR_ENGINES, settings),
        parser=_select("parser", "docling", PARSERS, settings),
        previews=_select("previews", "pdfium", PREVIEW_RENDERERS, settings),
        password_hasher=Argon2PasswordHasher(),
        cipher=secret_cipher(settings),
        totp=PyotpTotp(),
        oidc=(
            _select("oidc", "authlib", OIDC_PROVIDERS, settings) if settings.oidc_enabled else None
        ),
        search_index=search_index,
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
        closers=tuple(closers),
    )


def build_memory_container(clock: Clock | None = None) -> Container:
    """All ports on in-memory adapters, for tests and local experiments. Nothing persists."""
    database = MemoryDatabase()
    return Container(
        unit_of_work=MemoryUnitOfWorkFactory(database),
        event_bus=MemoryEventBus(database),
        object_store=MemoryObjectStore(),
        clock=clock or SystemClock(),
        ocr=FakeOcr(),
        parser=FakeParser(),
        previews=FakePreviewRenderer(),
        password_hasher=FakePasswordHasher(),
        cipher=FakeCipher(),
        totp=FakeTotp(),
        oidc=None,
        search_index=MemorySearchIndex(),
        language_model=None,
        embeddings=None,
    )


@dataclass(frozen=True)
class Services:
    """The use cases, wired to the container's adapters."""

    auth: AuthService
    oidc: OidcService | None  # with a configured provider
    users: UserService
    drawers: DrawerService
    master_data: MasterDataService
    documents: DocumentService
    pipeline: PipelineService
    maintenance: MaintenanceService
    indexing: IndexingService | None  # with a search index
    search: SearchService | None  # with a search index


# Time a step job may take beyond its time limit: downloads, uploads, preview, bookkeeping.
LEASE_MARGIN = timedelta(minutes=2)


def policy_of(settings: Settings) -> ClassificationPolicy:
    """Thresholds and limits of classification and attribute extraction."""
    return ClassificationPolicy(
        accept=settings.confidence_threshold,
        suggest_contact=settings.contact_suggest_threshold,
        input_budget=settings.llm_input_budget,
        max_tags=settings.llm_max_tags,
    )


def _prefix(value: str | None) -> str:
    return "" if not value else value + " "


def indexing_policy_of(settings: Settings) -> IndexingPolicy:
    return IndexingPolicy(
        max_text=settings.search_max_text,
        chunk_size=settings.search_chunk_size,
        max_chunks=settings.search_max_chunks,
        document_prefix=_prefix(settings.embedding_document_prefix),
        reconcile_interval=settings.search_reconcile_interval,
        # An indexing job may embed and write up to three times (see `IndexingService`).
        job_lease=3 * (settings.embedding_timeout + settings.meilisearch_task_timeout)
        + LEASE_MARGIN,
        rebuild_lease=settings.search_rebuild_timeout,
    )


def search_policy_of(settings: Settings) -> SearchPolicy:
    return SearchPolicy(
        semantic_ratio=settings.search_semantic_ratio,
        embed_timeout=settings.search_embed_timeout,
        query_prefix=_prefix(settings.embedding_query_prefix),
    )


def build_services(container: Container, settings: Settings | None = None) -> Services:
    """The use cases; tuning (retries, time limits, cleanup) from `settings`, or the defaults.

    OCR, parsing, classification and attribute extraction run on the container's adapters
    (without a language model, classification is uncertain). Rules and filing are
    placeholders until M7.
    """
    settings = settings or Settings.model_construct()
    uow, clock, store = container.unit_of_work, container.clock, container.object_store
    index, embeddings = container.search_index, container.embeddings
    executors: dict[Step, StepExecutor] = {step: PlaceholderStep() for step in PIPELINE[1:]}
    executors[Step.OCR] = OcrStep(store, container.ocr, container.previews)
    executors[Step.PARSE] = ParseStep(store, container.parser)
    policy = policy_of(settings)
    model = container.language_model
    executors[Step.CLASSIFY] = ClassifyStep(uow, store, model, clock, policy)
    executors[Step.EXTRACT_ATTRIBUTES] = ExtractAttributesStep(uow, store, model, clock, policy)
    auth = AuthService(
        uow,
        clock,
        hasher=container.password_hasher,
        cipher=container.cipher,
        totp=container.totp,
        sessions=SessionPolicy(
            idle=settings.session_idle_timeout, max_age=settings.session_max_age
        ),
    )
    return Services(
        auth=auth,
        oidc=(
            None
            if container.oidc is None
            else OidcService(
                uow,
                clock,
                auth,
                container.oidc,
                container.cipher,
                display_name=settings.oidc_display_name,
                auto_create=settings.oidc_auto_create,
            )
        ),
        users=UserService(uow, clock, container.password_hasher),
        drawers=DrawerService(uow, clock),
        master_data=MasterDataService(uow, clock, index_renames=index is not None),
        documents=DocumentService(uow, clock, store),
        pipeline=PipelineService(
            uow,
            clock,
            store,
            executors,
            pipeline_version=__version__,
            retry=RetryPolicy(
                max_attempts=settings.step_max_attempts, delay=settings.step_retry_delay
            ),
            # Longer than any step may take, so a running step never loses its claim.
            # A classification step may ask the model twice (once more after an invalid answer).
            lease=max(settings.ocr_timeout, settings.parse_timeout, 2 * settings.llm_timeout)
            + LEASE_MARGIN,
        ),
        maintenance=MaintenanceService(
            uow,
            clock,
            container.event_bus,
            interval=settings.cleanup_interval,
            retention=settings.retention,
            session_idle=settings.session_idle_timeout,
            object_store=store,
        ),
        indexing=(
            None
            if index is None
            else IndexingService(uow, clock, store, index, embeddings, indexing_policy_of(settings))
        ),
        search=(
            None
            if index is None
            else SearchService(uow, index, embeddings, search_policy_of(settings))
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
