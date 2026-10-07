"""The generic in-memory repository: rows of one table of the unit of work, keyed by UUID,
with optimistic locking on `version`."""

import copy
import dataclasses
from typing import TYPE_CHECKING, Any
from uuid import UUID

from papiq.adapters.outbound.memory.database import Table
from papiq.core.domain.errors import ConcurrencyError, ConflictError, NotFoundError

if TYPE_CHECKING:
    from papiq.adapters.outbound.memory.unit_of_work import MemoryUnitOfWork

_REMOVED = None


def _copy[E](entity: E) -> E:
    """A private copy. `replace` drops transient state such as recorded domain events."""
    return copy.deepcopy(dataclasses.replace(entity))  # type: ignore[type-var]


class MemoryRepository[K: UUID, E]:
    def __init__(self, uow: "MemoryUnitOfWork", table: Table) -> None:
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
        self._uow._check_open()
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
    def _copy(row: E) -> E:
        return _copy(row)

    @staticmethod
    def _id(entity: E) -> K:
        return entity.id  # type: ignore[attr-defined, no-any-return]

    def _remove_rows(self, rows: list[Any]) -> int:
        """Remove the given rows (from `_all`); returns how many."""
        for row in rows:
            self._uow._write(self._table, self._id(row), _REMOVED)
        return len(rows)

    def _touch(self, id: K, field: str, value: Any) -> None:
        """Move a usage timestamp forward without changing the version."""
        row = self._uow._row(self._table, id)
        if row is _REMOVED:
            return
        current = getattr(row, field)
        if current is None or current < value:
            changed = _copy(row)
            setattr(changed, field, value)
            self._uow._write(self._table, id, changed)
