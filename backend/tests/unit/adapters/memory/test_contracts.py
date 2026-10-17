"""The in-memory adapters pass every contract suite."""

import math
from collections.abc import Sequence

import pytest

from papiq.adapters.outbound.memory import (
    FAKE_GIRO_CODE,
    FAKE_VISION,
    BagOfWordsEmbeddings,
    FakeCipher,
    FakeClassifier,
    FakeCodeReader,
    FakeEmbeddings,
    FakeImageNormaliser,
    FakeLanguageModel,
    FakeLogoFetcher,
    FakeMetadataReader,
    FakeOcr,
    FakeOidcProvider,
    FakePageRenderer,
    FakeParser,
    FakePasswordHasher,
    FakeTextLocator,
    FakeTotp,
    ManualClock,
    MemoryDatabase,
    MemoryEventBus,
    MemoryObjectStore,
    MemorySearchIndex,
    MemoryUnitOfWorkFactory,
)
from papiq.core.domain.errors import EmbeddingsError, LanguageModelError
from papiq.core.ports import (
    Clock,
    DeliveryRetry,
    EmbeddingResult,
    EventBus,
    ObjectStore,
    StructuredRequest,
    UnitOfWorkFactory,
)
from papiq.core.ports.event_bus import DEFAULT_DELIVERY_RETRY
from tests.contracts.classifier import ClassifierContract
from tests.contracts.clock import ClockContract
from tests.contracts.event_bus import EventBusContract, EventBusFactory
from tests.contracts.identity import (
    Consent,
    IdentityRepositoriesContract,
    OidcProviderContract,
    PasswordHasherContract,
    SecretCipherContract,
    TotpContract,
)
from tests.contracts.images import ImageNormaliserContract
from tests.contracts.job_queue import JobQueueContract
from tests.contracts.language_model import EmbeddingsContract, LanguageModelContract
from tests.contracts.logos import LOGO_PNG, LogoFetcherContract
from tests.contracts.object_store import ObjectStoreContract
from tests.contracts.processing import (
    CodeReaderContract,
    MetadataReaderContract,
    OcrContract,
    PageRendererContract,
    ParserContract,
    TextLocatorContract,
)
from tests.contracts.rules import RuleRepositoriesContract
from tests.contracts.search_index import DIMENSIONS, SearchIndexContract
from tests.contracts.unit_of_work import UnitOfWorkContract
from tests.contracts.webhooks import WebhookRepositoryContract


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


class TestMemoryRuleRepositories(RuleRepositoriesContract):
    pass


class TestMemoryWebhookRepository(WebhookRepositoryContract):
    pass


class TestMemoryJobQueue(JobQueueContract):
    pass


class TestMemoryEventBus(EventBusContract):
    pass


class TestMemoryObjectStore(ObjectStoreContract):
    pass


class TestManualClock(ClockContract):
    pass


@pytest.fixture
def ocr() -> FakeOcr:
    return FakeOcr()


@pytest.fixture
def vision_ocr() -> FakeOcr:
    return FakeOcr(vision=FAKE_VISION)


@pytest.fixture
def code_reader() -> FakeCodeReader:
    return FakeCodeReader([FAKE_GIRO_CODE])


class TestFakeCodeReader(CodeReaderContract):
    pass


@pytest.fixture
def metadata_reader() -> FakeMetadataReader:
    return FakeMetadataReader()


class TestFakeMetadataReader(MetadataReaderContract):
    pass


@pytest.fixture
def image_normaliser() -> FakeImageNormaliser:
    return FakeImageNormaliser()


class TestFakeImageNormaliser(ImageNormaliserContract):
    pass


@pytest.fixture
def logo_fetcher() -> FakeLogoFetcher:
    return FakeLogoFetcher({"logo.example": LOGO_PNG})


class TestFakeLogoFetcher(LogoFetcherContract):
    pass


@pytest.fixture
def parser() -> FakeParser:
    return FakeParser()


@pytest.fixture
def page_renderer() -> FakePageRenderer:
    return FakePageRenderer()


class TestFakeOcr(OcrContract):
    pass


class TestFakeParser(ParserContract):
    pass


@pytest.fixture
def text_locator() -> FakeTextLocator:
    return FakeTextLocator(["Rechnung Nummer 4711\nPapiq Testdokument\nBetrag 123,45 EUR"])


class TestFakeTextLocator(TextLocatorContract):
    pass


class TestFakePageRenderer(PageRendererContract):
    pass


class TestMemoryIdentityRepositories(IdentityRepositoriesContract):
    pass


@pytest.fixture
def password_hasher() -> FakePasswordHasher:
    return FakePasswordHasher()


@pytest.fixture
def cipher() -> FakeCipher:
    return FakeCipher()


@pytest.fixture
def totp() -> FakeTotp:
    return FakeTotp()


class TestFakePasswordHasher(PasswordHasherContract):
    pass


class TestFakeCipher(SecretCipherContract):
    pass


class TestFakeTotp(TotpContract):
    pass


@pytest.fixture
def fake_idp() -> FakeOidcProvider:
    return FakeOidcProvider()


@pytest.fixture
def oidc_provider(fake_idp: FakeOidcProvider) -> FakeOidcProvider:
    return fake_idp


@pytest.fixture
def oidc_consent(fake_idp: FakeOidcProvider) -> Consent:
    return lambda url, subject, username: fake_idp.consent(url, subject, username=username)


class TestFakeOidcProvider(OidcProviderContract):
    pass


@pytest.fixture
def language_model() -> FakeLanguageModel:
    return FakeLanguageModel(lambda request: {"answer": "ok"})


@pytest.fixture
def failing_language_model() -> FakeLanguageModel:
    def fail(request: StructuredRequest) -> str:
        raise LanguageModelError("unreachable")

    return FakeLanguageModel(fail)


class TestFakeLanguageModel(LanguageModelContract):
    pass


class FailingEmbeddings(FakeEmbeddings):
    async def embed(self, texts: Sequence[str]) -> EmbeddingResult:
        if texts:
            raise EmbeddingsError("unreachable")
        return await super().embed(texts)


@pytest.fixture
def embeddings() -> FakeEmbeddings:
    return FakeEmbeddings()


@pytest.fixture
def failing_embeddings() -> FakeEmbeddings:
    return FailingEmbeddings()


class TestFakeEmbeddings(EmbeddingsContract):
    pass


def dot(left: Sequence[float], right: Sequence[float]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))


class TestBagOfWordsEmbeddings(EmbeddingsContract):
    @pytest.fixture
    def embeddings(self) -> BagOfWordsEmbeddings:
        return BagOfWordsEmbeddings()

    async def test_texts_with_words_in_common_are_closer(self) -> None:
        model = BagOfWordsEmbeddings()
        result = await model.embed(
            ["Rechnung für Strom", "Stromrechnung März", "Mietvertrag Wohnung"]
        )
        bill, compound, lease = result.vectors
        assert math.isclose(sum(value * value for value in bill), 1.0)
        assert dot(bill, compound) > dot(bill, lease)
        assert dot(compound, bill) > 0.2  # the letters "rech", "chn", ... are shared

    async def test_a_text_without_words_still_has_a_vector(self) -> None:
        (vector,) = (await BagOfWordsEmbeddings().embed(["--"])).vectors
        assert math.isclose(sum(value * value for value in vector), 1.0)


@pytest.fixture
def search_index() -> MemorySearchIndex:
    return MemorySearchIndex(dimensions=DIMENSIONS)


class TestMemorySearchIndex(SearchIndexContract):
    pass


@pytest.fixture
def classifier() -> FakeClassifier:
    return FakeClassifier()


class TestFakeClassifier(ClassifierContract):
    pass
