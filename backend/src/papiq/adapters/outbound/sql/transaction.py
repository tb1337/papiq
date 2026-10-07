"""The database transaction of one unit of work, shared by its repositories."""

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import CursorResult, Executable, Result
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from papiq.adapters.outbound.sql.database import Database
from papiq.core.domain.errors import ConcurrencyError, ConflictError

log = logging.getLogger(__name__)

# Postgres: deadlock_detected, serialization_failure.
_CONCURRENCY_SQLSTATES = {"40P01", "40001"}


class Transaction:
    """One connection, one transaction. Reads run at once; the first write starts the write
    transaction (see `Database.begin_write`).

    A write that fails leaves the transaction failed: on Postgres it is aborted anyway, so on
    both databases only rollback remains. A broken constraint raises ConflictError; a deadlock
    or serialization failure (Postgres) raises ConcurrencyError.
    """

    def __init__(self, database: Database) -> None:
        self.database = database
        self._connection: AsyncConnection | None = None
        self._writing = False
        self._closed = False
        self._failed = False

    async def open(self) -> None:
        self._check_usable()
        if self._connection is None:
            self._connection = await self.database.engine.connect()

    async def read(self, statement: Executable) -> Result[Any]:
        return await self._usable().execute(statement)

    async def write(
        self,
        statement: Executable,
        parameters: Sequence[Mapping[str, Any]] | None = None,
    ) -> CursorResult[Any]:
        connection = self._usable()
        if not self._writing:
            await self.database.begin_write(connection)
            self._writing = True
        try:
            return await connection.execute(statement, parameters)
        except DBAPIError as error:
            self._failed = True
            if isinstance(error, IntegrityError):
                # The database's message names tables and constraints: logs only.
                log.info("constraint violated", extra={"error": str(error.orig)})
                raise ConflictError("the change conflicts with existing data") from error
            if getattr(error.orig, "sqlstate", None) in _CONCURRENCY_SQLSTATES:
                log.info("concurrent transaction", extra={"error": str(error.orig)})
                raise ConcurrencyError("a concurrent change interfered; try again") from error
            raise

    async def commit(self) -> None:
        connection = self._usable()
        try:
            await connection.commit()
        finally:
            await self._close()

    async def rollback(self) -> None:
        self._check_open()
        try:
            if self._connection is not None:
                await self._connection.rollback()
        finally:
            await self._close()

    @property
    def closed(self) -> bool:
        return self._closed

    def _usable(self) -> AsyncConnection:
        self._check_usable()
        if self._connection is None:
            raise RuntimeError("the unit of work has not been entered")
        return self._connection

    def _check_usable(self) -> None:
        self._check_open()
        if self._failed:
            raise RuntimeError("a write of this unit of work failed; it can only roll back")

    def _check_open(self) -> None:
        if self._closed:
            raise RuntimeError("the unit of work is closed")

    async def _close(self) -> None:
        self._closed = True
        if self._writing:
            self.database.end_write()
        if self._connection is not None:
            connection, self._connection = self._connection, None
            await connection.close()
