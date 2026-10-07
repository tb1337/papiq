"""Composition root: choose the configured adapter for each port.

Adapters register their factories in the tables below, keyed by the configured adapter name.
A port whose configured adapter does not exist yet fails with AdapterNotAvailableError.
"""

import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import timedelta

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
    MemoryUnitOfWorkFactory,
)
from papiq.adapters.outbound.ocrmypdf import OcrmypdfEngine
from papiq.adapters.outbound.oidc import AuthlibOidcProvider
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
from papiq.core.services.documents import DocumentService
from papiq.core.services.drawers import DrawerService
from papiq.core.services.maintenance import MaintenanceService
from papiq.core.services.master_data import MasterDataService
from papiq.core.services.oidc import OidcService
from papiq.core.services.pipeline import PipelineService, PlaceholderStep, RetryPolicy, StepExecutor
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
SEARCH_INDEXES: dict[str, Factory[SearchIndex]] = {}
LANGUAGE_MODELS: dict[str, Factory[LanguageModel]] = {}
EMBEDDINGS: dict[str, Factory[Embeddings]] = {}


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
        search_index=None,
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


# Time a step job may take beyond its time limit: downloads, uploads, preview, bookkeeping.
LEASE_MARGIN = timedelta(minutes=2)


def build_services(container: Container, settings: Settings | None = None) -> Services:
    """The use cases; tuning (retries, time limits, cleanup) from `settings`, or the defaults.

    OCR and parsing run on the container's adapters. Classification, attributes, rules and
    filing are placeholders until M5 and M7.
    """
    settings = settings or Settings.model_construct()
    uow, clock, store = container.unit_of_work, container.clock, container.object_store
    executors: dict[Step, StepExecutor] = {step: PlaceholderStep() for step in PIPELINE[1:]}
    executors[Step.OCR] = OcrStep(store, container.ocr, container.previews)
    executors[Step.PARSE] = ParseStep(store, container.parser)
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
        master_data=MasterDataService(uow, clock),
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
            lease=max(settings.ocr_timeout, settings.parse_timeout) + LEASE_MARGIN,
        ),
        maintenance=MaintenanceService(
            uow,
            clock,
            container.event_bus,
            interval=settings.cleanup_interval,
            retention=settings.retention,
            session_idle=settings.session_idle_timeout,
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
