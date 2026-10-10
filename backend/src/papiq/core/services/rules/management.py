"""Creating, changing, enabling, disabling and deleting rules.

Who may do what:

- A user rule belongs to its owner, who changes it. Admins read and change every rule (Tobi,
  08.10.2026); the references of a user rule are checked for its owner, also when an admin
  changes it (it files only into drawers its owner may write to). Other users do not learn that
  someone else's rule exists (NotFoundError).
- Global rules are read by everyone and changed by admins only.
"""

import builtins

from papiq.core.domain.errors import NotFoundError, PermissionDeniedError
from papiq.core.domain.ids import RuleId, UserId
from papiq.core.domain.permissions import drawer_access
from papiq.core.domain.rules import Rule, RuleDefinition, RuleScope, RuleVersion, check_fields
from papiq.core.domain.users import User
from papiq.core.ports import Clock, UnitOfWork, UnitOfWorkFactory
from papiq.core.services._access import load_actor


class RuleService:
    def __init__(self, uow: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    async def list(
        self,
        actor: UserId,
        *,
        scope: RuleScope | None = None,
        include_disabled: bool = True,
        all_users: bool = False,
    ) -> list[Rule]:
        """The caller's rules and the global rules, in the order they are applied.
        `all_users`: the rules of every user instead of the caller's (admins only)."""
        async with self._uow() as uow:
            user = await load_actor(uow, actor)
            if all_users and not user.is_active_admin:
                raise PermissionDeniedError("only admins list the rules of all users")
            rules = await uow.rules.list_for(
                owners=None if all_users else (user.id,),
                include_global=scope is not RuleScope.USER,
            )
        return sorted(
            (
                rule
                for rule in rules
                if (scope is None or rule.scope is scope) and (include_disabled or rule.enabled)
            ),
            key=lambda rule: rule.order,
        )

    async def get(self, actor: UserId, id: RuleId) -> Rule:
        async with self._uow() as uow:
            rule = await visible_rule(uow, await load_actor(uow, actor), id)
            if rule.deleted_at is not None:
                raise NotFoundError("rule", id)
            return rule

    async def versions(self, actor: UserId, id: RuleId) -> "builtins.list[RuleVersion]":
        """All versions, oldest first; also of a deleted rule, so the processing log can be
        read."""
        async with self._uow() as uow:
            await visible_rule(uow, await load_actor(uow, actor), id)
            return await uow.rules.versions(id)

    async def version(self, actor: UserId, id: RuleId, number: int) -> RuleVersion:
        async with self._uow() as uow:
            await visible_rule(uow, await load_actor(uow, actor), id)
            return await uow.rules.get_version(id, number)

    async def create(self, actor: UserId, scope: RuleScope, definition: RuleDefinition) -> Rule:
        """A global rule needs an admin; a user rule belongs to the caller."""
        async with self._uow() as uow:
            user = await load_actor(uow, actor)
            if scope is RuleScope.GLOBAL and not user.is_active_admin:
                raise PermissionDeniedError("only admins create global rules")
            rule = Rule.create(
                scope=scope,
                owner_id=None if scope is RuleScope.GLOBAL else user.id,
                definition=definition,
                by=user.id,
                now=self._clock.now(),
            )
            await check_references(uow, rule.definition, owner=user if rule.owner_id else None)
            await uow.rules.add(rule)
            await uow.commit()
        return rule

    async def change(self, actor: UserId, id: RuleId, definition: RuleDefinition) -> Rule:
        """A new version; the scope stays."""
        async with self._uow() as uow:
            user = await load_actor(uow, actor)
            rule = await _managed_rule(uow, user, id)
            rule.change(definition, user.id, self._clock.now())
            await check_references(uow, definition, owner=await _owner(uow, rule))
            await uow.rules.update(rule)
            await uow.commit()
        return rule

    async def set_enabled(self, actor: UserId, id: RuleId, enabled: bool) -> Rule:
        """Enabling checks the references again (they may have been deleted meanwhile)."""
        async with self._uow() as uow:
            user = await load_actor(uow, actor)
            rule = await _managed_rule(uow, user, id)
            if enabled:
                await check_references(uow, rule.definition, owner=await _owner(uow, rule))
                rule.enable(self._clock.now())
            else:
                rule.disable(self._clock.now())
            await uow.rules.update(rule)
            await uow.commit()
        return rule

    async def delete(self, actor: UserId, id: RuleId) -> None:
        """Soft delete: the rule never runs again, its versions stay readable."""
        async with self._uow() as uow:
            rule = await _managed_rule(uow, await load_actor(uow, actor), id)
            rule.delete(self._clock.now())
            await uow.rules.update(rule)
            await uow.commit()


async def check_references(
    uow: UnitOfWork, definition: RuleDefinition, *, owner: User | None
) -> None:
    """Everything the definition refers to exists (NotFoundError otherwise), field
    conditions and values fit their field (ValidationError), and the `owner` of a user
    rule may file into its drawer (PermissionDeniedError; checked again whenever it runs)."""
    references = definition.references()
    for contact in references.contacts:
        await uow.contacts.get(contact)
    for document_type in references.document_types:
        await uow.document_types.get(document_type)
    for tag in references.tags:
        await uow.tags.get(tag)
    if references.fields:
        fields = {field.id: field for field in await uow.fields.list_all()}
        for field_id in references.fields:
            if field_id not in fields:
                raise NotFoundError("field", field_id)
        check_fields(definition, fields)
    for drawer_id in references.drawers:
        drawer = await uow.drawers.find(drawer_id)
        access = None if drawer is None or owner is None else drawer_access(owner, drawer)
        if drawer is None or access is None:
            raise NotFoundError("drawer", drawer_id)
        if not access.can_write:
            raise PermissionDeniedError(f"no write access to drawer '{drawer.name}'")


async def visible_rule(uow: UnitOfWork, user: User, id: RuleId) -> Rule:
    """Global rules and the caller's own; all rules for admins. Includes deleted rules."""
    rule = await uow.rules.find(id)
    if rule is None or not (
        rule.scope is RuleScope.GLOBAL or rule.owner_id == user.id or user.is_active_admin
    ):
        raise NotFoundError("rule", id)
    return rule


async def _managed_rule(uow: UnitOfWork, user: User, id: RuleId) -> Rule:
    rule = await visible_rule(uow, user, id)
    if rule.deleted_at is not None:
        raise NotFoundError("rule", id)
    if user.is_active_admin:
        return rule
    if rule.scope is RuleScope.GLOBAL:
        raise PermissionDeniedError("only admins change global rules")
    if rule.owner_id != user.id:
        raise PermissionDeniedError("only the owner changes a user rule")
    return rule


async def _owner(uow: UnitOfWork, rule: Rule) -> User | None:
    """The owner of a user rule, for checking its references; None for a global rule."""
    return None if rule.owner_id is None else await uow.users.get(rule.owner_id)
