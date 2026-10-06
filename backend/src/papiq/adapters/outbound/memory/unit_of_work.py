"""In-memory unit of work: changes are buffered and applied to the database on commit.

Behaves like a database transaction with read-committed isolation and optimistic locking:
reads see committed data plus the unit's own changes; commit checks that every row written
still has the version it had when the unit first touched it, and that uniqueness holds.
"""

import copy
import dataclasses
from collections.abc import Collection, Hashable, Iterable, Mapping
from datetime import datetime, timedelta
from types import TracebackType
from typing import Any, Self
from uuid import UUID

from papiq.adapters.outbound.memory.database import (
    ATTRIBUTES,
    CONTACTS,
    DOCUMENT_TYPES,
    DOCUMENTS,
    DRAWERS,
    TAGS,
    USERS,
    MemoryDatabase,
    Table,
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

_REMOVED = None


def _copy[E](entity: E) -> E:
    """A private copy. `replace` drops transient state such as recorded domain events."""
    return copy.deepcopy(dataclasses.replace(entity))  # type: ignore[type-var]


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
        self.outbox = MemoryOutbox(self)
        self.jobs = MemoryJobQueue(self)

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

    async def commit(self) -> None:
        self._check_open()
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
        self._db.outbox.extend(self._events)
        self._db.jobs.update(self._jobs)
        self._closed = True

    async def rollback(self) -> None:
        self._check_open()
        self._closed = True

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


class MemoryRepository[K: UUID, E]:
    def __init__(self, uow: MemoryUnitOfWork, table: Table) -> None:
        self._uow = uow
        self._table = table

    async def get(self, id: K) -> E:
        entity = await self.find(id)
        if entity is None:
            raise NotFoundError(self._table.name, id)
        return entity

    async def find(self, id: K) -> E | None:
        self._uow._check_open()
        row = self._uow._row(self._table, id)
        return None if row is _REMOVED else _copy(row)

    async def add(self, entity: E) -> None:
        id = self._id(entity)
        if self._uow._row(self._table, id) is not _REMOVED:
            raise ConflictError(f"{self._table.name} {id} already exists")
        self._uow._unique_or_fail(self._table, [*self._uow._rows(self._table), entity])
        self._uow._write(self._table, id, _copy(entity))

    async def update(self, entity: E) -> None:
        id = self._id(entity)
        current = self._uow._row(self._table, id)
        if current is _REMOVED:
            raise NotFoundError(self._table.name, id)
        if current.version != entity.version:  # type: ignore[attr-defined]
            raise ConcurrencyError(f"{self._table.name} {id} was changed concurrently")
        others = [row for row in self._uow._rows(self._table) if row is not current]
        self._uow._unique_or_fail(self._table, [*others, entity])
        entity.version += 1  # type: ignore[attr-defined]
        self._uow._write(self._table, id, _copy(entity))

    async def list_all(self) -> list[E]:
        return [_copy(row) for row in self._all()]

    def _all(self) -> list[E]:
        self._uow._check_open()
        return self._uow._rows(self._table)

    @staticmethod
    def _id(entity: E) -> K:
        return entity.id  # type: ignore[attr-defined, no-any-return]


class MemoryNamedRepository[K: UUID, E: MasterData](MemoryRepository[K, E]):
    async def find_by_name(self, name: str) -> E | None:
        key = name_key(name)
        return next((_copy(row) for row in self._all() if name_key(row.name) == key), None)


class MemoryUserRepository(MemoryRepository[UserId, User]):
    async def find_by_username(self, username: str) -> User | None:
        key = name_key(username)
        return next((_copy(row) for row in self._all() if name_key(row.username) == key), None)


class MemoryDrawerRepository(MemoryRepository[DrawerId, Drawer]):
    async def get_default(self, owner: UserId) -> Drawer:
        for row in self._all():
            if row.owner_id == owner and row.is_default:
                return _copy(row)
        raise NotFoundError("default drawer of user", owner)

    async def list_accessible(self, user: UserId) -> list[Drawer]:
        return [_copy(row) for row in self._all() if row.owner_id == user or user in row.shares]


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


class MemoryProcessingLog:
    def __init__(self, uow: MemoryUnitOfWork) -> None:
        self._uow = uow

    async def append(self, run: StepRun) -> None:
        self._uow._check_open()
        self._uow._log.append(run)

    async def list_for(self, document: DocumentId) -> list[StepRun]:
        self._uow._check_open()
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
        return claimed

    async def complete(self, job: JobId) -> None:
        current = await self.get(job)
        self._write(dataclasses.replace(current, status=JobStatus.DONE, locked_until=None))

    async def reschedule(self, job: JobId, *, run_at: datetime, error: str) -> None:
        current = await self.get(job)
        self._write(
            dataclasses.replace(
                current,
                status=JobStatus.QUEUED,
                run_at=require_utc(run_at, "run_at"),
                locked_until=None,
                last_error=error,
            )
        )

    async def fail(self, job: JobId, *, error: str) -> None:
        current = await self.get(job)
        self._write(
            dataclasses.replace(
                current, status=JobStatus.FAILED, locked_until=None, last_error=error
            )
        )

    async def get(self, job: JobId) -> Job:
        self._uow._check_open()
        found = self._uow._jobs.get(job) or self._uow._db.jobs.get(job)
        if found is None:
            raise NotFoundError("job", job)
        return found

    def _all(self) -> list[Job]:
        self._uow._check_open()
        jobs = {**self._uow._db.jobs, **self._uow._jobs}
        return list(jobs.values())

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
