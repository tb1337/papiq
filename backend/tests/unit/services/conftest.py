from collections.abc import Mapping
from dataclasses import dataclass, field

import pytest

from papiq.adapters.outbound.memory import (
    FakeCipher,
    FakeImageNormaliser,
    FakeOidcProvider,
    FakePasswordHasher,
    FakeTotp,
    FakeWebhookSender,
    ManualClock,
    MemoryDatabase,
    MemoryObjectStore,
    MemoryUnitOfWorkFactory,
)
from papiq.core.domain.documents import Document
from papiq.core.domain.events import DomainEvent
from papiq.core.domain.identity import Credential, check_new_password
from papiq.core.domain.pipeline import NODES, Node, NodeResult
from papiq.core.domain.spaces import Space
from papiq.core.domain.users import Role, User
from papiq.core.ports.logos import LogoFetcher
from papiq.core.ports.ocr import VisionModel
from papiq.core.services.auth import AuthService
from papiq.core.services.documents import DocumentService
from papiq.core.services.logos import LogoService
from papiq.core.services.master_data import MasterDataService
from papiq.core.services.oidc import OidcService
from papiq.core.services.pipeline import NodeExecutor, PipelineService, PlaceholderNode
from papiq.core.services.spaces import SpaceService
from papiq.core.services.users import UserService
from papiq.core.services.webhooks import WebhookDeliveryService, WebhookPolicy, WebhookService
from tests import builders


class Returns:
    """Node executor that returns a fixed result."""

    def __init__(self, result: NodeResult) -> None:
        self.result = result
        self.calls = 0

    async def run(self, document: Document) -> NodeResult:
        self.calls += 1
        return self.result


class Raises:
    """Node executor that raises `times` times, then returns OK."""

    def __init__(self, times: int) -> None:
        self.times = times
        self.calls = 0

    async def run(self, document: Document) -> NodeResult:
        self.calls += 1
        if self.calls <= self.times:
            raise OSError("scanner glitch")
        return builders.OK


@dataclass
class World:
    """Services on in-memory adapters, plus helpers to seed and inspect state."""

    clock: ManualClock = field(default_factory=lambda: ManualClock(builders.NOW))
    database: MemoryDatabase = field(default_factory=MemoryDatabase)
    object_store: MemoryObjectStore = field(default_factory=MemoryObjectStore)
    executors: dict[Node, NodeExecutor] = field(
        default_factory=lambda: {
            node: PlaceholderNode() for node in NODES if node is not Node.RECEIVE
        }
    )
    hasher: FakePasswordHasher = field(default_factory=FakePasswordHasher)
    cipher: FakeCipher = field(default_factory=FakeCipher)
    totp: FakeTotp = field(default_factory=FakeTotp)
    idp: FakeOidcProvider = field(default_factory=FakeOidcProvider)
    sender: FakeWebhookSender = field(default_factory=FakeWebhookSender)
    images: FakeImageNormaliser = field(default_factory=FakeImageNormaliser)

    @property
    def uow(self) -> MemoryUnitOfWorkFactory:
        return MemoryUnitOfWorkFactory(self.database)

    @property
    def users(self) -> UserService:
        return UserService(self.uow, self.clock, self.hasher)

    @property
    def auth(self) -> AuthService:
        return AuthService(
            self.uow, self.clock, hasher=self.hasher, cipher=self.cipher, totp=self.totp
        )

    def oidc(self, *, auto_create: bool = False) -> OidcService:
        return OidcService(
            self.uow,
            self.clock,
            self.auth,
            self.idp,
            self.cipher,
            display_name="Test IdP",
            auto_create=auto_create,
        )

    @property
    def spaces(self) -> SpaceService:
        return SpaceService(self.uow, self.clock)

    @property
    def master_data(self) -> MasterDataService:
        return MasterDataService(self.uow, self.clock, builders.TEMPLATES, store=self.object_store)

    @property
    def logos(self) -> LogoService:
        return LogoService(self.uow, self.clock, self.object_store, self.images)

    def fetching(self, fetcher: LogoFetcher) -> tuple[MasterDataService, LogoService]:
        """Master data and logos with fetching on."""
        master = MasterDataService(
            self.uow, self.clock, builders.TEMPLATES, store=self.object_store, fetch_logos=True
        )
        return master, LogoService(self.uow, self.clock, self.object_store, self.images, fetcher)

    @property
    def documents(self) -> DocumentService:
        return DocumentService(self.uow, self.clock, self.object_store)

    @property
    def webhooks(self) -> WebhookService:
        return WebhookService(self.uow, self.clock, self.cipher)

    def webhook_delivery(self, policy: WebhookPolicy | None = None) -> WebhookDeliveryService:
        return WebhookDeliveryService(self.uow, self.clock, self.cipher, self.sender, policy)

    def pipeline(
        self,
        executors: Mapping[Node, NodeExecutor] | None = None,
        *,
        vision: VisionModel | None = None,
        facts: NodeExecutor | None = None,
    ) -> PipelineService:
        return PipelineService(
            self.uow,
            self.clock,
            self.object_store,
            {**self.executors, **(executors or {})},
            pipeline_version="test",
            vision=vision,
            facts=facts,
        )

    async def user(self, name: str | None = None, role: Role = Role.USER) -> User:
        """A user with their default space, seeded directly."""
        user = builders.user(name, role=role)
        async with self.uow() as uow:
            await uow.users.add(user)
            await uow.spaces.add(builders.default_space(user))
            await uow.commit()
        return user

    async def account(
        self, name: str | None = None, role: Role = Role.USER, password: str = builders.PASSWORD
    ) -> User:
        """A user with default space and password."""
        user = await self.user(name, role)
        async with self.uow() as uow:
            await uow.credentials.add(
                Credential(
                    user_id=user.id,
                    password_hash=await self.hasher.hash(check_new_password(password, "-")),
                )
            )
            await uow.commit()
        return user

    async def default_space(self, user: User) -> Space:
        async with self.uow() as uow:
            return await uow.spaces.get_default(user.id)

    async def drain(self, pipeline: PipelineService | None = None) -> int:
        """Run due jobs until none is left; returns how many ran."""
        pipeline = pipeline or self.pipeline()
        count = 0
        while await pipeline.run_next_job():
            count += 1
        return count

    def events(self) -> list[DomainEvent]:
        return list(self.database.outbox)

    def event_types(self) -> list[str]:
        return [event.type for event in self.database.outbox]


@pytest.fixture
def world() -> World:
    return World()
