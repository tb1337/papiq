from collections.abc import Callable
from types import TracebackType
from typing import Protocol, Self

from papiq.core.ports.event_bus import Outbox
from papiq.core.ports.identity import (
    ApiTokenRepository,
    CredentialRepository,
    ExternalIdentityRepository,
    LoginFailureRepository,
    SessionRepository,
)
from papiq.core.ports.job_queue import JobQueue
from papiq.core.ports.repository import (
    AttributeDefinitionRepository,
    ContactRepository,
    DocumentRepository,
    DocumentTypeRepository,
    DrawerRepository,
    ProcessingLog,
    RuleApplicationRepository,
    RuleRepository,
    TagRepository,
    UserRepository,
    WebhookRepository,
)


class UnitOfWork(Protocol):
    """One transaction over repositories (rules and webhooks included), processing log, outbox,
    job queue and identity.

    State change, events and jobs are committed together or not at all:

        async with uow_factory() as uow:
            document = await uow.documents.get(document_id)
            ...
            await uow.documents.update(document)
            await uow.outbox.add(document.pull_events())
            await uow.commit()

    Leaving the block without `commit` (or with an exception) rolls back. Changes are visible
    to other units of work only after commit; within the unit, reads see its own changes.
    After commit or rollback the unit is closed and cannot be used again.
    """

    @property
    def users(self) -> UserRepository: ...
    @property
    def drawers(self) -> DrawerRepository: ...
    @property
    def contacts(self) -> ContactRepository: ...
    @property
    def document_types(self) -> DocumentTypeRepository: ...
    @property
    def tags(self) -> TagRepository: ...
    @property
    def attributes(self) -> AttributeDefinitionRepository: ...
    @property
    def documents(self) -> DocumentRepository: ...
    @property
    def processing_log(self) -> ProcessingLog: ...
    @property
    def rules(self) -> RuleRepository: ...
    @property
    def rule_applications(self) -> RuleApplicationRepository: ...
    @property
    def webhooks(self) -> WebhookRepository: ...
    @property
    def outbox(self) -> Outbox: ...
    @property
    def jobs(self) -> JobQueue: ...
    @property
    def credentials(self) -> CredentialRepository: ...
    @property
    def sessions(self) -> SessionRepository: ...
    @property
    def api_tokens(self) -> ApiTokenRepository: ...
    @property
    def external_identities(self) -> ExternalIdentityRepository: ...
    @property
    def login_failures(self) -> LoginFailureRepository: ...

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    async def lock(self, name: str) -> None:
        """Hold an exclusive lock on `name` until this unit ends (commit or rollback). Other
        units that lock the same name wait meanwhile. For work outside the database that must
        not interleave, e.g. storing and deleting the same original. Lock at most one name per
        unit, before its other writes."""
        ...

    async def commit(self) -> None:
        """Make all changes durable and visible. ConflictError or ConcurrencyError if a
        uniqueness rule or a version check fails; then nothing is stored."""
        ...

    async def rollback(self) -> None: ...


type UnitOfWorkFactory = Callable[[], UnitOfWork]
