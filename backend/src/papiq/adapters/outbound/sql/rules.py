"""Rule repositories on SQL tables: `rules` with the current version number, every version in
`rule_versions` (JSON definitions), applications in `rule_applications`."""

from collections.abc import Collection, Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import Row, and_, delete, insert, or_, select, tuple_

from papiq.adapters.outbound.sql import tables as t
from papiq.adapters.outbound.sql.repositories import SqlRepository, Values
from papiq.core.domain.errors import NotFoundError
from papiq.core.domain.ids import DocumentId, RuleApplicationId, RuleId, UserId
from papiq.core.domain.json_value import JsonValue
from papiq.core.domain.rules import (
    ApplicationStatus,
    Rule,
    RuleApplication,
    RuleScope,
    RuleVersion,
    definition_from_json,
    definition_to_json,
)


class SqlRuleRepository(SqlRepository[RuleId, Rule]):
    kind = "rule"
    table = t.rules

    async def add(self, rule: Rule) -> None:
        await super().add(rule)
        await self._store_version(rule.current)

    async def update(self, rule: Rule) -> None:
        await super().update(rule)
        await self._store_version(rule.current)

    async def list_for(
        self, *, owners: Collection[UserId] | None, include_global: bool
    ) -> list[Rule]:
        rules = t.rules
        users = rules.c.scope == RuleScope.USER.value
        if owners is not None:
            users = and_(users, rules.c.owner_id.in_(list(owners)))
        scopes = [users]
        if include_global:
            scopes.append(rules.c.scope == RuleScope.GLOBAL.value)
        return await self._load(and_(rules.c.deleted_at.is_(None), or_(*scopes)))

    async def versions(self, id: RuleId) -> list[RuleVersion]:
        await self.get(id)
        versions = t.rule_versions
        rows = await self._tx.read(
            select(versions).where(versions.c.rule_id == id).order_by(versions.c.number)
        )
        return [_version(row) for row in rows]

    async def get_version(self, id: RuleId, number: int) -> RuleVersion:
        await self.get(id)
        versions = t.rule_versions
        row = (
            await self._tx.read(
                select(versions).where(versions.c.rule_id == id, versions.c.number == number)
            )
        ).first()
        if row is None:
            raise NotFoundError(f"version {number} of rule", id)
        return _version(row)

    async def remove_for_owner(self, owner: UserId) -> int:
        """Versions and applications go with their rule (ON DELETE CASCADE); the user's
        applications of other rules go with the user."""
        applications = t.rule_applications
        await self._tx.write(delete(applications).where(applications.c.user_id == owner))
        result = await self._tx.write(delete(t.rules).where(t.rules.c.owner_id == owner))
        return int(result.rowcount)

    async def _entities(self, rows: Sequence[Row[Any]]) -> list[Rule]:
        if not rows:
            return []
        versions = t.rule_versions
        # Only the current versions: older ones are neither needed nor read again.
        current = {
            (row.rule_id, row.number): _version(row)
            for row in (
                await self._tx.read(
                    select(versions).where(
                        tuple_(versions.c.rule_id, versions.c.number).in_(
                            [(row.id, row.current_version) for row in rows]
                        )
                    )
                )
            ).all()
        }
        return [
            Rule(
                id=RuleId(row.id),
                scope=RuleScope(row.scope),
                owner_id=None if row.owner_id is None else UserId(row.owner_id),
                current=current[(row.id, row.current_version)],
                enabled=row.enabled,
                disabled_reason=row.disabled_reason,
                deleted_at=row.deleted_at,
                created_at=row.created_at,
                updated_at=row.updated_at,
                version=row.version,
            )
            for row in rows
        ]

    def _values(self, entity: Rule) -> Values:
        return {
            "id": entity.id,
            "scope": entity.scope.value,
            "owner_id": entity.owner_id,
            "current_version": entity.current.number,
            "enabled": entity.enabled,
            "disabled_reason": entity.disabled_reason,
            "deleted_at": entity.deleted_at,
            "created_at": entity.created_at,
            "updated_at": entity.updated_at,
            "version": entity.version,
        }

    async def _store_version(self, version: RuleVersion) -> None:
        versions = t.rule_versions
        exists = (
            await self._tx.read(
                select(versions.c.number).where(
                    versions.c.rule_id == version.rule_id, versions.c.number == version.number
                )
            )
        ).first()
        if exists is not None:
            return
        data = definition_to_json(version.definition)
        await self._tx.write(
            insert(versions).values(
                rule_id=version.rule_id,
                number=version.number,
                name=data["name"],
                priority=data["priority"],
                triggers=data["triggers"],
                conditions=data["conditions"],
                actions=data["actions"],
                created_at=version.created_at,
                created_by=version.created_by,
            )
        )


def _version(row: Row[Any]) -> RuleVersion:
    definition = definition_from_json(
        {
            "name": row.name,
            "priority": row.priority,
            "triggers": row.triggers,
            "conditions": row.conditions,
            "actions": row.actions,
        }
    )
    return RuleVersion(
        rule_id=RuleId(row.rule_id),
        number=row.number,
        definition=definition,
        created_at=row.created_at,
        created_by=None if row.created_by is None else UserId(row.created_by),
    )


class SqlRuleApplicationRepository(SqlRepository[RuleApplicationId, RuleApplication]):
    kind = "rule application"
    table = t.rule_applications

    def _entity(self, row: Row[Any]) -> RuleApplication:
        return RuleApplication(
            id=RuleApplicationId(row.id),
            rule_id=RuleId(row.rule_id),
            rule_version=row.rule_version,
            user_id=UserId(row.user_id),
            documents=tuple(DocumentId(UUID(item)) for item in row.documents),
            accept_conflicts=frozenset(DocumentId(UUID(item)) for item in row.accept_conflicts),
            status=ApplicationStatus(row.status),
            position=row.position,
            applied=row.applied,
            unchanged=row.unchanged,
            skipped=[(DocumentId(UUID(item[0])), str(item[1])) for item in row.skipped],
            error=row.error,
            created_at=row.created_at,
            finished_at=row.finished_at,
            version=row.version,
        )

    def _values(self, entity: RuleApplication) -> Values:
        skipped: list[JsonValue] = [[str(id), reason] for id, reason in entity.skipped]
        return {
            "id": entity.id,
            "rule_id": entity.rule_id,
            "rule_version": entity.rule_version,
            "user_id": entity.user_id,
            "status": entity.status.value,
            "documents": [str(id) for id in entity.documents],
            "accept_conflicts": sorted(str(id) for id in entity.accept_conflicts),
            "position": entity.position,
            "applied": entity.applied,
            "unchanged": entity.unchanged,
            "skipped": skipped,
            "error": entity.error,
            "created_at": entity.created_at,
            "finished_at": entity.finished_at,
            "version": entity.version,
        }
