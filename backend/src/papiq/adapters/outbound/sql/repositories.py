"""The generic repository on SQL tables, with the explicit mapping between domain objects and
rows; the repositories by area (users, spaces, master data, documents) build on it.

`update` is optimistic: `UPDATE ... WHERE id = :id AND version = :version`. If no row matches,
the entity is missing (NotFoundError) or was changed by someone else (ConcurrencyError). On
Postgres, a concurrent update of the same row waits for the other transaction and then finds
the new version; on SQLite, writers are serialized by the write lock.
"""

from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import timedelta
from typing import Any, ClassVar
from uuid import UUID

from sqlalchemy import ColumnElement, Row, Table, delete, insert, select, true, update

from papiq.adapters.outbound.sql.transaction import Transaction
from papiq.core.domain.errors import ConcurrencyError, NotFoundError

type Values = dict[str, Any]


MICROSECOND = timedelta(microseconds=1)


class SqlRepository[K: UUID, E]:
    """Common part: a main table with `id` and `version`, optionally child tables."""

    kind: ClassVar[str]
    table: ClassVar[Table]

    def __init__(self, transaction: Transaction) -> None:
        self._tx = transaction

    async def get(self, id: K) -> E:
        entity = await self.find(id)
        if entity is None:
            raise NotFoundError(self.kind, id)
        return entity

    async def find(self, id: K) -> E | None:
        found = await self._load(self.table.c.id == id)
        return found[0] if found else None

    async def add(self, entity: E) -> None:
        await self._tx.write(insert(self.table).values(self._values(entity)))
        await self._write_children(entity)

    async def update(self, entity: E) -> None:
        id, version = self._id(entity), self._version(entity)
        result = await self._tx.write(
            update(self.table)
            .where(self.table.c.id == id, self.table.c.version == version)
            .values({**self._values(entity), "version": version + 1})
        )
        if result.rowcount == 0:
            exists = await self._tx.read(select(self.table.c.id).where(self.table.c.id == id))
            if exists.first() is None:
                raise NotFoundError(self.kind, id)
            raise ConcurrencyError(f"{self.kind} {id} was changed concurrently")
        entity.version = version + 1  # type: ignore[attr-defined]
        await self._delete_children([id])
        await self._write_children(entity)

    async def list_all(self) -> list[E]:
        return await self._load(true())

    async def remove(self, id: K) -> None:
        """Child tables go with the row (ON DELETE CASCADE)."""
        result = await self._tx.write(delete(self.table).where(self.table.c.id == id))
        if result.rowcount == 0:
            raise NotFoundError(self.kind, id)

    async def _load(self, where: ColumnElement[bool]) -> list[E]:
        rows = (await self._tx.read(select(self.table).where(where))).all()
        return await self._entities(rows)

    async def _entities(self, rows: Sequence[Row[Any]]) -> list[E]:
        return [self._entity(row) for row in rows]

    # --- mapping, implemented per entity -------------------------------------------------------

    def _values(self, entity: E) -> Values:
        """Column values of the main table, including id and version."""
        raise NotImplementedError

    def _entity(self, row: Row[Any]) -> E:
        raise NotImplementedError

    async def _write_children(self, entity: E) -> None:
        """Insert the rows of child tables."""

    async def _delete_children(self, ids: list[K]) -> None:
        """Delete the rows of child tables."""

    @staticmethod
    def _id(entity: E) -> K:
        return entity.id  # type: ignore[attr-defined, no-any-return]

    @staticmethod
    def _version(entity: E) -> int:
        return entity.version  # type: ignore[attr-defined, no-any-return]

    async def _insert_many(self, table: Table, rows: list[Values]) -> None:
        if rows:
            await self._tx.write(insert(table), rows)

    async def _children(
        self, table: Table, parent: str, ids: Iterable[UUID]
    ) -> dict[UUID, list[Row[Any]]]:
        """Rows of a child table, grouped by the parent column."""
        grouped: dict[UUID, list[Row[Any]]] = defaultdict(list)
        id_list = list(ids)
        if id_list:
            column = table.c[parent]
            for row in (await self._tx.read(select(table).where(column.in_(id_list)))).all():
                grouped[row._mapping[parent]].append(row)
        return grouped
