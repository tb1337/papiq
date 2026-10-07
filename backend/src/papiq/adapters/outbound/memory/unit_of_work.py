"""In-memory unit of work: changes are buffered and applied to the database on commit.

Behaves like a database transaction with read-committed isolation and optimistic locking:
reads see committed data plus the unit's own changes; commit checks that every row written
still has the version it had when the unit first touched it, and that uniqueness holds.
"""

import asyncio
import copy
import dataclasses
from collections.abc import Collection, Hashable, Iterable, Mapping
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Any, Self
from uuid import UUID

from papiq.adapters.outbound.memory.database import (
    API_TOKENS,
    ATTRIBUTES,
    CONTACTS,
    CREDENTIALS,
    DOCUMENT_TYPES,
    DOCUMENTS,
    DRAWERS,
    EXTERNAL_IDENTITIES,
    LOGIN_FAILURES,
    RULE_APPLICATIONS,
    RULES,
    SESSIONS,
    TAGS,
    USERS,
    MemoryDatabase,
    Table,
)
from papiq.adapters.outbound.memory.identity import (
    MemoryApiTokenRepository,
    MemoryCredentialRepository,
    MemoryExternalIdentityRepository,
    MemoryLoginFailureRepository,
    MemorySessionRepository,
)
from papiq.adapters.outbound.memory.rows import _REMOVED, MemoryRepository, _copy
from papiq.adapters.outbound.memory.rules import (
    MemoryRuleApplicationRepository,
    MemoryRuleRepository,
)
from papiq.core.domain.attributes import AttributeDefinition
from papiq.core.domain.documents import Document, Sha256
from papiq.core.domain.drawers import Drawer
from papiq.core.domain.errors import ConcurrencyError, ConflictError, NotFoundError
from papiq.core.domain.events import DomainEvent
from papiq.core.domain.ids import (
    AttributeId,
    ContactId,
    DocumentId,
    DocumentTypeId,
    DrawerId,
    JobId,
    TagId,
    UserId,
    new_id,
)
from papiq.core.domain.jobs import Job, JobStatus
from papiq.core.domain.json_value import JsonValue
from papiq.core.domain.master_data import Contact, DocumentType, MasterData, Tag
from papiq.core.domain.pipeline import Lane, StepRun
from papiq.core.domain.users import User
from papiq.core.domain.validation import name_key, require_utc
from papiq.core.ports.repository import DocumentFilter


class MemoryUnitOfWork:
    def __init__(self, database: MemoryDatabase) -> None:
        self._db = database
        self._closed = False
        self._writes: dict[str, dict[UUID, Any]] = {}
        self._tables: dict[str, Table] = {}
        self._base_versions: dict[tuple[str, UUID], int | None] = {}
        self._log: list[StepRun] = []
        self._events: list[DomainEvent] = []
        self._jobs: dict[JobId, Job] = {}
        self._job_base: dict[JobId, Job | None] = {}
        self._purged_jobs: set[JobId] = set()
        self._held: asyncio.Lock | None = None

        self.users = MemoryUserRepository(self, USERS)
        self.drawers = MemoryDrawerRepository(self, DRAWERS)
        self.contacts = MemoryNamedRepository[ContactId, Contact](self, CONTACTS)
        self.document_types = MemoryNamedRepository[DocumentTypeId, DocumentType](
            self, DOCUMENT_TYPES
        )
        self.tags = MemoryNamedRepository[TagId, Tag](self, TAGS)
        self.attributes = MemoryNamedRepository[AttributeId, AttributeDefinition](self, ATTRIBUTES)
        self.documents = MemoryDocumentRepository(self, DOCUMENTS)
        self.processing_log = MemoryProcessingLog(self)
        self.rules = MemoryRuleRepository(self, RULES)
        self.rule_applications = MemoryRuleApplicationRepository(self, RULE_APPLICATIONS)
        self.outbox = MemoryOutbox(self)
        self.jobs = MemoryJobQueue(self)
        self.credentials = MemoryCredentialRepository(self, CREDENTIALS)
        self.sessions = MemorySessionRepository(self, SESSIONS)
        self.api_tokens = MemoryApiTokenRepository(self, API_TOKENS)
        self.external_identities = MemoryExternalIdentityRepository(self, EXTERNAL_IDENTITIES)
        self.login_failures = MemoryLoginFailureRepository(self, LOGIN_FAILURES)

    # --- transaction ----------------------------------------------------------------------------

    async def __aenter__(self) -> Self:
        self._check_open()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if not self._closed:
            await self.rollback()

    async def lock(self, name: str) -> None:
        self._check_open()
        if self._held is not None:
            raise RuntimeError("a unit of work locks at most one name")
        lock = self._db.locks.setdefault(name, asyncio.Lock())
        await lock.acquire()
        self._held = lock

    def _release(self) -> None:
        if self._held is not None:
            self._held.release()
            self._held = None

    async def commit(self) -> None:
        self._check_open()
        try:
            self._commit()
        finally:
            self._release()

    def _commit(self) -> None:
        self._check_versions()
        self._check_uniqueness()
        self._check_jobs()
        for table_name, writes in self._writes.items():
            rows = self._db.tables.setdefault(table_name, {})
            for id, row in writes.items():
                if row is _REMOVED:
                    rows.pop(id, None)
                else:
                    rows[id] = row
        self._db.processing_log.extend(self._log)
        removed = {id for id, row in self._writes.get(DOCUMENTS.name, {}).items() if row is None}
        self._db.processing_log[:] = [
            entry for entry in self._db.processing_log if entry.document_id not in removed
        ]
        self._db.outbox.extend(self._events)
        recorded_at = datetime.now(UTC)
        self._db.recorded_at.update((event.id, recorded_at) for event in self._events)
        self._db.jobs.update(self._jobs)
        for id in self._purged_jobs:
            self._db.jobs.pop(id, None)
        self._closed = True

    async def rollback(self) -> None:
        self._check_open()
        self._closed = True
        self._release()

    def _check_open(self) -> None:
        if self._closed:
            raise RuntimeError("the unit of work is closed")

    def _check_versions(self) -> None:
        for (table_name, id), base in self._base_versions.items():
            committed = self._db.tables.get(table_name, {}).get(id)
            if (None if committed is None else committed.version) != base:
                raise ConcurrencyError(f"{table_name} {id} was changed concurrently")

    def _check_uniqueness(self) -> None:
        for table_name in self._writes:
            self._unique_or_fail(self._tables[table_name], self._rows(self._tables[table_name]))

    def _check_jobs(self) -> None:
        for id, base in self._job_base.items():
            if self._db.jobs.get(id) != base:
                raise ConcurrencyError(f"job {id} was changed concurrently")
        for job in self._jobs.values():
            if job.dedup_key is not None and job.status.is_active:
                for other in self._db.jobs.values():
                    if (
                        other.id not in self._jobs
                        and other.id not in self._purged_jobs
                        and other.dedup_key == job.dedup_key
                        and other.status.is_active
                    ):
                        raise ConflictError(f"an active job with key {job.dedup_key} exists")

    # --- rows -----------------------------------------------------------------------------------

    def _rows(self, table: Table) -> list[Any]:
        """Committed rows with this unit's changes applied (not copied)."""
        committed = self._db.tables.get(table.name, {})
        writes = self._writes.get(table.name, {})
        rows = [writes.get(id, row) for id, row in committed.items()]
        rows += [row for id, row in writes.items() if id not in committed]
        return [row for row in rows if row is not _REMOVED]

    def _row(self, table: Table, id: UUID) -> Any:
        writes = self._writes.get(table.name, {})
        if id in writes:
            return writes[id]
        return self._db.tables.get(table.name, {}).get(id)

    def _write(self, table: Table, id: UUID, row: Any) -> None:
        self._check_open()
        key = (table.name, id)
        if key not in self._base_versions:
            committed = self._db.tables.get(table.name, {}).get(id)
            self._base_versions[key] = None if committed is None else committed.version
        self._tables[table.name] = table
        self._writes.setdefault(table.name, {})[id] = row

    @staticmethod
    def _unique_or_fail(table: Table, rows: Iterable[Any]) -> None:
        seen: set[Hashable] = set()
        for row in rows:
            for key in table.unique_keys(row):
                if key in seen:
                    raise ConflictError(f"{table.name} already exists: {key}")
                seen.add(key)


class MemoryNamedRepository[K: UUID, E: MasterData](MemoryRepository[K, E]):
    async def find_by_name(self, name: str) -> E | None:
        key = name_key(name)
        return next((_copy(row) for row in self._all() if name_key(row.name) == key), None)

    async def remove(self, id: K) -> None:
        await self.get(id)
        self._uow._write(self._table, id, _REMOVED)


class MemoryUserRepository(MemoryRepository[UserId, User]):
    async def find_by_username(self, username: str) -> User | None:
        key = name_key(username)
        return next((_copy(row) for row in self._all() if name_key(row.username) == key), None)

    async def remove(self, id: UserId) -> None:
        await self.get(id)
        self._uow._write(self._table, id, _REMOVED)


class MemoryDrawerRepository(MemoryRepository[DrawerId, Drawer]):
    async def get_default(self, owner: UserId) -> Drawer:
        for row in self._all():
            if row.owner_id == owner and row.is_default:
                return _copy(row)
        raise NotFoundError("default drawer of user", owner)

    async def list_accessible(self, user: UserId) -> list[Drawer]:
        return [_copy(row) for row in self._all() if row.owner_id == user or user in row.shares]

    async def remove(self, id: DrawerId) -> None:
        await self.get(id)
        self._uow._write(self._table, id, _REMOVED)


class MemoryDocumentRepository(MemoryRepository[DocumentId, Document]):
    async def find_by_sha256(self, owner: UserId, sha256: Sha256) -> Document | None:
        for row in self._all():
            if row.owner_id == owner and row.sha256 == sha256:
                return _copy(row)
        return None

    async def list_visible_to(self, user: UserId) -> list[Document]:
        readable = {drawer.id for drawer in await self._uow.drawers.list_accessible(user)}
        return [
            _copy(row)
            for row in self._all()
            if row.owner_id == user or (row.lane is Lane.GREEN and row.drawer_id in readable)
        ]

    async def remove(self, id: DocumentId) -> None:
        if self._uow._row(self._table, id) is _REMOVED:
            raise NotFoundError(self._table.name, id)
        self._uow._write(self._table, id, _REMOVED)

    async def attribute_in_use(
        self,
        attribute: AttributeId,
        *,
        values: Collection[str] | None = None,
        outside_types: Collection[DocumentTypeId] | None = None,
    ) -> bool:
        return any(
            attribute in row.attributes
            and (values is None or row.attributes[attribute] in set(values))
            and (outside_types is None or row.document_type_id not in set(outside_types))
            for row in self._all()
        )

    async def query_visible(
        self,
        user: UserId,
        filter: DocumentFilter,
        *,
        before: DocumentId | None = None,
        limit: int,
    ) -> list[Document]:
        matching = [
            document
            for document in await self.list_visible_to(user)
            if (before is None or document.id < before)
            and (filter.contact is None or document.contact_id == filter.contact)
            and (filter.document_type is None or document.document_type_id == filter.document_type)
            and filter.tags <= document.tag_ids
            and (filter.drawer is None or document.drawer_id == filter.drawer)
            and (filter.lanes is None or document.lane in filter.lanes)
        ]
        return sorted(matching, key=lambda document: document.id, reverse=True)[:limit]

    async def exists(
        self,
        *,
        owner: UserId | None = None,
        drawer: DrawerId | None = None,
        contact: ContactId | None = None,
        document_type: DocumentTypeId | None = None,
        tag: TagId | None = None,
        attribute: AttributeId | None = None,
        sha256: Sha256 | None = None,
    ) -> bool:
        criteria = (owner, drawer, contact, document_type, tag, attribute, sha256)
        if all(value is None for value in criteria):
            raise ValueError("exists needs at least one criterion")
        return any(
            (owner is None or row.owner_id == owner)
            and (drawer is None or row.drawer_id == drawer)
            and (contact is None or row.contact_id == contact)
            and (document_type is None or row.document_type_id == document_type)
            and (tag is None or tag in row.tag_ids)
            and (attribute is None or attribute in row.attributes)
            and (sha256 is None or row.sha256 == sha256)
            for row in self._all()
        )


class MemoryProcessingLog:
    def __init__(self, uow: MemoryUnitOfWork) -> None:
        self._uow = uow

    async def append(self, run: StepRun) -> None:
        self._uow._check_open()
        self._uow._log.append(run)

    async def list_for(self, document: DocumentId) -> list[StepRun]:
        self._uow._check_open()
        if self._uow._row(DOCUMENTS, document) is _REMOVED:
            return []
        entries = [*self._uow._db.processing_log, *self._uow._log]
        return [entry for entry in entries if entry.document_id == document]


class MemoryOutbox:
    def __init__(self, uow: MemoryUnitOfWork) -> None:
        self._uow = uow

    async def add(self, events: Iterable[DomainEvent]) -> None:
        self._uow._check_open()
        self._uow._events.extend(events)


class MemoryJobQueue:
    def __init__(self, uow: MemoryUnitOfWork) -> None:
        self._uow = uow

    async def enqueue(
        self,
        kind: str,
        payload: Mapping[str, JsonValue],
        *,
        run_at: datetime,
        dedup_key: str | None = None,
    ) -> JobId | None:
        if dedup_key is not None and any(
            job.dedup_key == dedup_key and job.status.is_active for job in self._all()
        ):
            return None
        job = Job(
            id=JobId(new_id()),
            kind=kind,
            payload=copy.deepcopy(dict(payload)),
            dedup_key=dedup_key,
            status=JobStatus.QUEUED,
            attempts=0,
            run_at=require_utc(run_at, "run_at"),
        )
        self._write(job)
        return job.id

    async def claim(
        self, *, now: datetime, lease: timedelta, kinds: Collection[str] | None = None
    ) -> Job | None:
        due = [
            job
            for job in self._all()
            if (kinds is None or job.kind in kinds)
            and (
                (job.status is JobStatus.QUEUED and job.run_at <= now)
                or (
                    job.status is JobStatus.RUNNING
                    and job.locked_until is not None
                    and job.locked_until <= now
                )
            )
        ]
        if not due:
            return None
        job = min(due, key=lambda job: (job.run_at, job.id))
        claimed = dataclasses.replace(
            job, status=JobStatus.RUNNING, attempts=job.attempts + 1, locked_until=now + lease
        )
        self._write(claimed)
        return copy.deepcopy(claimed)

    async def complete(self, job: Job) -> None:
        current = await self._claimed(job)
        self._write(dataclasses.replace(current, status=JobStatus.DONE, locked_until=None))

    async def reschedule(self, job: Job, *, run_at: datetime, error: str) -> None:
        current = await self._claimed(job)
        self._write(
            dataclasses.replace(
                current,
                status=JobStatus.QUEUED,
                run_at=require_utc(run_at, "run_at"),
                locked_until=None,
                last_error=error,
            )
        )

    async def release(self, job: Job, *, run_at: datetime, error: str) -> None:
        current = await self._claimed(job)
        self._write(
            dataclasses.replace(
                current,
                status=JobStatus.QUEUED,
                releases=current.releases + 1,
                run_at=require_utc(run_at, "run_at"),
                locked_until=None,
                last_error=error,
            )
        )

    async def fail(self, job: Job, *, error: str) -> None:
        current = await self._claimed(job)
        self._write(
            dataclasses.replace(
                current, status=JobStatus.FAILED, locked_until=None, last_error=error
            )
        )

    async def _claimed(self, job: Job) -> Job:
        current = await self.get(job.id)
        if current.status is not JobStatus.RUNNING or current.attempts != job.attempts:
            raise ConcurrencyError(f"job {job.id} is no longer held by this claim")
        return current

    async def get(self, job: JobId) -> Job:
        self._uow._check_open()
        found = self._uow._jobs.get(job) or self._uow._db.jobs.get(job)
        if found is None or job in self._uow._purged_jobs:
            raise NotFoundError("job", job)
        return copy.deepcopy(found)

    async def purge(self, *, before: datetime) -> int:
        finished = [
            job.id
            for job in self._all()
            if job.status in {JobStatus.DONE, JobStatus.FAILED} and job.run_at < before
        ]
        self._uow._purged_jobs.update(finished)
        return len(finished)

    def _all(self) -> list[Job]:
        self._uow._check_open()
        jobs = {**self._uow._db.jobs, **self._uow._jobs}
        return [job for id, job in jobs.items() if id not in self._uow._purged_jobs]

    def _write(self, job: Job) -> None:
        self._uow._check_open()
        if job.id not in self._uow._job_base:
            self._uow._job_base[job.id] = self._uow._db.jobs.get(job.id)
        self._uow._jobs[job.id] = job


class MemoryUnitOfWorkFactory:
    """Creates units of work on one shared in-memory database."""

    def __init__(self, database: MemoryDatabase) -> None:
        self._database = database

    def __call__(self) -> MemoryUnitOfWork:
        return MemoryUnitOfWork(self._database)
