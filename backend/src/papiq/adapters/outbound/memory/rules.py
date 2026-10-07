"""In-memory rule repositories, on the tables of the in-memory unit of work."""

from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID, uuid5

from papiq.adapters.outbound.memory.database import RULE_APPLICATIONS, RULE_VERSIONS, Table
from papiq.adapters.outbound.memory.rows import _REMOVED, MemoryRepository
from papiq.core.domain.errors import NotFoundError
from papiq.core.domain.ids import RuleApplicationId, RuleId, UserId
from papiq.core.domain.rules import Rule, RuleApplication, RuleScope, RuleVersion

if TYPE_CHECKING:
    from collections.abc import Collection

    from papiq.adapters.outbound.memory.unit_of_work import MemoryUnitOfWork


@dataclass
class VersionRow:
    """A stored rule version; `version` only for the unit of work's bookkeeping."""

    id: UUID
    item: RuleVersion
    version: int = 1


def version_row_id(rule: RuleId, number: int) -> UUID:
    return uuid5(rule, str(number))


class MemoryRuleRepository(MemoryRepository[RuleId, Rule]):
    def __init__(self, uow: "MemoryUnitOfWork", table: Table) -> None:
        super().__init__(uow, table)
        self._versions = MemoryRepository[UUID, VersionRow](uow, RULE_VERSIONS)
        self._applications = MemoryRepository[RuleApplicationId, RuleApplication](
            uow, RULE_APPLICATIONS
        )

    async def add(self, rule: Rule) -> None:
        await super().add(rule)
        await self._store_version(rule.current)

    async def update(self, rule: Rule) -> None:
        await super().update(rule)
        await self._store_version(rule.current)

    async def list_for(
        self, *, owners: "Collection[UserId] | None", include_global: bool
    ) -> list[Rule]:
        wanted = None if owners is None else set(owners)
        return [
            self._copy(row)
            for row in self._all()
            if row.deleted_at is None
            and (
                (row.scope is RuleScope.GLOBAL and include_global)
                or (row.scope is RuleScope.USER and (wanted is None or row.owner_id in wanted))
            )
        ]

    async def versions(self, id: RuleId) -> list[RuleVersion]:
        await self.get(id)
        found = [row.item for row in self._versions._all() if row.item.rule_id == id]
        return sorted(found, key=lambda version: version.number)

    async def get_version(self, id: RuleId, number: int) -> RuleVersion:
        await self.get(id)
        row = await self._versions.find(version_row_id(id, number))
        if row is None:
            raise NotFoundError(f"version {number} of rule", id)
        return row.item

    async def remove_for_owner(self, owner: UserId) -> int:
        rules = [row for row in self._all() if row.owner_id == owner]
        ids = {row.id for row in rules}
        self._versions._remove_rows(
            [row for row in self._versions._all() if row.item.rule_id in ids]
        )
        self._applications._remove_rows(
            [row for row in self._applications._all() if row.rule_id in ids or row.user_id == owner]
        )
        return self._remove_rows(rules)

    async def _store_version(self, version: RuleVersion) -> None:
        id = version_row_id(version.rule_id, version.number)
        if self._uow._row(RULE_VERSIONS, id) is _REMOVED:
            await self._versions.add(VersionRow(id, version))


class MemoryRuleApplicationRepository(MemoryRepository[RuleApplicationId, RuleApplication]):
    pass
