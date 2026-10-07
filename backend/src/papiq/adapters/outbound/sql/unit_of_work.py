"""Unit of work on SQLite or Postgres: one connection and one transaction.

Outbox events are kept in memory and written in `commit`, right before COMMIT:

- On Postgres, the transaction first takes an advisory lock that is held until the commit
  ends. Transactions that write events therefore commit one after another, each with higher
  `outbox.seq` values than all events committed before it. So `seq` follows commit order and a
  subscriber's position in the outbox never passes an event that is committed later. Without
  the lock, a transaction that drew a lower `seq` could commit after a higher one was already
  delivered, and that event would be skipped.
- On SQLite, writers are serialized by the database anyway. Writing the events late also keeps
  a unit that only adds events from holding the single write lock while it is open.
"""

from collections.abc import Iterable
from datetime import UTC, datetime
from types import TracebackType
from typing import Self

from sqlalchemy import func, insert, select

from papiq.adapters.outbound.sql import events, tables
from papiq.adapters.outbound.sql.database import Database
from papiq.adapters.outbound.sql.identity import (
    SqlApiTokenRepository,
    SqlCredentialRepository,
    SqlExternalIdentityRepository,
    SqlLoginFailureRepository,
    SqlSessionRepository,
)
from papiq.adapters.outbound.sql.job_queue import SqlJobQueue
from papiq.adapters.outbound.sql.repositories import (
    SqlAttributeRepository,
    SqlContactRepository,
    SqlDocumentRepository,
    SqlDocumentTypeRepository,
    SqlDrawerRepository,
    SqlProcessingLog,
    SqlTagRepository,
    SqlUserRepository,
)
from papiq.adapters.outbound.sql.transaction import Transaction
from papiq.core.domain.events import DomainEvent

# Key of the Postgres advisory lock that orders commits with events ("papiq:outbox").
OUTBOX_LOCK_KEY = 0x7061_7069_713A_6F62


class SqlOutbox:
    def __init__(self, transaction: Transaction) -> None:
        self._tx = transaction
        self.pending: list[DomainEvent] = []

    async def add(self, events: Iterable[DomainEvent]) -> None:
        if self._tx.closed:
            raise RuntimeError("the unit of work is closed")
        self.pending.extend(events)

    async def flush(self) -> None:
        """Write the pending events; called by `commit` as the last write."""
        if not self.pending:
            return
        if not self._tx.database.is_sqlite:
            await self._tx.write(select(func.pg_advisory_xact_lock(OUTBOX_LOCK_KEY)))
        recorded_at = datetime.now(UTC)
        await self._tx.write(
            insert(tables.outbox),
            [
                {
                    "event_id": event.id,
                    "type": event.type,
                    "occurred_at": event.occurred_at,
                    "payload": events.encode(event),
                    "recorded_at": recorded_at,
                }
                for event in self.pending
            ],
        )
        self.pending = []


class SqlUnitOfWork:
    def __init__(self, database: Database) -> None:
        self._tx = Transaction(database)
        self.users = SqlUserRepository(self._tx)
        self.drawers = SqlDrawerRepository(self._tx)
        self.contacts = SqlContactRepository(self._tx)
        self.document_types = SqlDocumentTypeRepository(self._tx)
        self.tags = SqlTagRepository(self._tx)
        self.attributes = SqlAttributeRepository(self._tx)
        self.documents = SqlDocumentRepository(self._tx)
        self.processing_log = SqlProcessingLog(self._tx)
        self.outbox = SqlOutbox(self._tx)
        self.jobs = SqlJobQueue(self._tx)
        self.credentials = SqlCredentialRepository(self._tx)
        self.sessions = SqlSessionRepository(self._tx)
        self.api_tokens = SqlApiTokenRepository(self._tx)
        self.external_identities = SqlExternalIdentityRepository(self._tx)
        self.login_failures = SqlLoginFailureRepository(self._tx)

    async def __aenter__(self) -> Self:
        await self._tx.open()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if not self._tx.closed:
            await self.rollback()

    async def commit(self) -> None:
        await self.outbox.flush()
        await self._tx.commit()

    async def rollback(self) -> None:
        self.outbox.pending = []
        await self._tx.rollback()


class SqlUnitOfWorkFactory:
    """Creates units of work on one database."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def __call__(self) -> SqlUnitOfWork:
        return SqlUnitOfWork(self._database)
