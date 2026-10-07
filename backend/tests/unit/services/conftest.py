from collections.abc import Mapping
from dataclasses import dataclass, field

import pytest

from papiq.adapters.outbound.memory import (
    FakeCipher,
    FakePasswordHasher,
    FakeTotp,
    ManualClock,
    MemoryDatabase,
    MemoryObjectStore,
    MemoryUnitOfWorkFactory,
)
from papiq.core.domain.documents import Document
from papiq.core.domain.drawers import Drawer
from papiq.core.domain.events import DomainEvent
from papiq.core.domain.identity import Credential, check_new_password
from papiq.core.domain.pipeline import PIPELINE, Step, StepResult
from papiq.core.domain.users import Role, User
from papiq.core.services.auth import AuthService
from papiq.core.services.documents import DocumentService
from papiq.core.services.drawers import DrawerService
from papiq.core.services.master_data import MasterDataService
from papiq.core.services.pipeline import PipelineService, PlaceholderStep, StepExecutor
from papiq.core.services.users import UserService
from tests import builders


class Returns:
    """Step executor that returns a fixed result."""

    def __init__(self, result: StepResult) -> None:
        self.result = result
        self.calls = 0

    async def run(self, document: Document) -> StepResult:
        self.calls += 1
        return self.result


class Raises:
    """Step executor that raises `times` times, then returns OK."""

    def __init__(self, times: int) -> None:
        self.times = times
        self.calls = 0

    async def run(self, document: Document) -> StepResult:
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
    executors: dict[Step, StepExecutor] = field(
        default_factory=lambda: {step: PlaceholderStep() for step in PIPELINE[1:]}
    )
    hasher: FakePasswordHasher = field(default_factory=FakePasswordHasher)
    cipher: FakeCipher = field(default_factory=FakeCipher)
    totp: FakeTotp = field(default_factory=FakeTotp)

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

    @property
    def drawers(self) -> DrawerService:
        return DrawerService(self.uow, self.clock)

    @property
    def master_data(self) -> MasterDataService:
        return MasterDataService(self.uow, self.clock)

    @property
    def documents(self) -> DocumentService:
        return DocumentService(self.uow, self.clock)

    def pipeline(self, executors: Mapping[Step, StepExecutor] | None = None) -> PipelineService:
        return PipelineService(
            self.uow,
            self.clock,
            self.object_store,
            {**self.executors, **(executors or {})},
            pipeline_version="test",
        )

    async def user(self, name: str | None = None, role: Role = Role.USER) -> User:
        """A user with their default drawer, seeded directly."""
        user = builders.user(name, role=role)
        async with self.uow() as uow:
            await uow.users.add(user)
            await uow.drawers.add(builders.default_drawer(user))
            await uow.commit()
        return user

    async def account(
        self, name: str | None = None, role: Role = Role.USER, password: str = builders.PASSWORD
    ) -> User:
        """A user with default drawer and password."""
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

    async def default_drawer(self, user: User) -> Drawer:
        async with self.uow() as uow:
            return await uow.drawers.get_default(user.id)

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
