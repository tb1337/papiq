"""Engines for SQLite and Postgres, and how the adapter starts transactions on each.

Reads run with read-committed semantics on both databases. On Postgres that is the default
isolation level. On SQLite the driver runs in autocommit mode, so reads see the latest
committed state; a unit of work starts its transaction with `BEGIN IMMEDIATE` right before
its first write. That takes SQLite's single write lock up front: a read transaction is never
upgraded to a write transaction, which would fail with SQLITE_BUSY instead of waiting.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import URL, event
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

# SQLite engine settings, applied to every connection.
SQLITE_BUSY_TIMEOUT = timedelta(seconds=15)
_SQLITE_PRAGMAS = (
    "PRAGMA journal_mode=WAL",  # readers do not block the writer and vice versa
    "PRAGMA synchronous=NORMAL",  # durable with WAL except on power loss, much faster
    "PRAGMA foreign_keys=ON",
    f"PRAGMA busy_timeout={int(SQLITE_BUSY_TIMEOUT.total_seconds() * 1000)}",
)


class Database:
    """An async engine for SQLite or Postgres. Create it with `sqlite()` or `postgres()`."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    @classmethod
    def sqlite(cls, path: Path) -> "Database":
        """A SQLite database file (created by `migrate`). Only on a local volume: WAL mode
        does not work on network file systems."""
        engine = create_async_engine(URL.create("sqlite+aiosqlite", database=str(path)))
        event.listen(engine.sync_engine, "connect", _configure_sqlite)
        return cls(engine)

    @classmethod
    def postgres(cls, *, host: str, port: int, name: str, user: str, password: str) -> "Database":
        url = URL.create(
            "postgresql+asyncpg",
            username=user,
            password=password,
            host=host,
            port=port,
            database=name,
        )
        return cls(create_async_engine(url))

    @property
    def is_sqlite(self) -> bool:
        return self.engine.dialect.name == "sqlite"

    async def begin_write(self, connection: AsyncConnection) -> None:
        """Start the write transaction on `connection`; call before its first write."""
        if self.is_sqlite:
            await connection.exec_driver_sql("BEGIN IMMEDIATE")

    @asynccontextmanager
    async def reading(self) -> AsyncIterator[AsyncConnection]:
        """A connection for reads; nothing is committed."""
        async with self.engine.connect() as connection:
            yield connection

    @asynccontextmanager
    async def writing(self) -> AsyncIterator[AsyncConnection]:
        """A write transaction, committed when the block ends without an exception."""
        async with self.engine.connect() as connection:
            await self.begin_write(connection)
            yield connection
            await connection.commit()

    async def dispose(self) -> None:
        await self.engine.dispose()


def _configure_sqlite(dbapi_connection: Any, _record: object) -> None:
    # Autocommit in the driver: the adapter emits BEGIN itself (see the module docstring).
    dbapi_connection.isolation_level = None
    cursor = dbapi_connection.cursor()
    try:
        for pragma in _SQLITE_PRAGMAS:
            cursor.execute(pragma)
    finally:
        cursor.close()
