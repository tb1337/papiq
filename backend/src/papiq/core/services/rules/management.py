"""Creating, changing, enabling, disabling and deleting rules.

Who may do what:

- A user rule belongs to its owner, who changes it. Admins read and change every rule (Tobi,
  08.10.2026); the references of a user rule are checked for its owner, also when an admin
  changes it (it files only into spaces its owner may write to). Other users do not learn that
  someone else's rule exists (NotFoundError).
- Global rules are read by everyone and changed by admins only.
"""

import builtins
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace

from papiq.core.domain.documents import Document
from papiq.core.domain.errors import (
    ConflictError,
    MissingValueError,
    NotFoundError,
    PermissionDeniedError,
    SpaceWriteDeniedError,
    ValidationError,
)
from papiq.core.domain.fields import FieldDefinition, FieldType
from papiq.core.domain.ids import DocumentId, DocumentTypeId, RuleId, UserId
from papiq.core.domain.permissions import space_access
from papiq.core.domain.rules import (
    ForceReview,
    Rule,
    RuleDefinition,
    RuleScope,
    RuleVersion,
    SetField,
    SetTitle,
    check_fields,
)
from papiq.core.domain.templates import (
    FIELD,
    NAMES,
    TemplateValue,
    rename_field_key,
    template_values,
    unknown_values,
    writing_templates,
)
from papiq.core.domain.templates import uses_field as field_used
from papiq.core.domain.users import User
from papiq.core.ports import Clock, TemplateRenderer, UnitOfWork, UnitOfWorkFactory
from papiq.core.services._access import load_actor, readable_document


@dataclass(frozen=True)
class TemplatePreview:
    """What a template gives for a document: the values it uses, the rendered text
    (whitespace collapsed, as in a title), or the missing value or the error that stops it.
    `values`: what the document has, each value as a template writes it (by its path, such as
    `field.la_brutto`); None without a document."""

    used: tuple[str, ...] = ()
    rendered: str | None = None
    missing: str | None = None
    error: str | None = None
    values: Mapping[str, str] | None = None


class RuleService:
    def __init__(self, uow: UnitOfWorkFactory, clock: Clock, templates: TemplateRenderer) -> None:
        self._uow = uow
        self._clock = clock
        self._templates = templates

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
            await check_templates(uow, self._templates, rule.definition)
            await uow.rules.add(rule)
            await uow.commit()
        return rule

    async def preview_template(
        self,
        actor: UserId,
        template: str,
        *,
        document: DocumentId | None = None,
        document_type: DocumentTypeId | None = None,
    ) -> TemplatePreview:
        """`template` checked (syntax, values; the fields of `document_type`, or of the
        document's type, else all) and rendered with a document the caller may read, as it
        is, with the document's values whatever the template. Nothing is stored."""
        async with self._uow() as uow:
            user = await load_actor(uow, actor)
            found = None if document is None else (await readable_document(uow, user, document))[0]
            kind = document_type or (None if found is None else found.document_type_id)
            if kind is not None:
                await uow.document_types.get(kind)
            fields = await uow.fields.list_all()
            applying = [item for item in fields if kind is None or item.applies_to(kind)]
            values = None if found is None else await document_values(uow, found, kind, applying)
        written = None if values is None else self._written(values)
        try:
            used = self._templates.check(template)
        except ValidationError as invalid:
            return TemplatePreview(error=str(invalid), values=written)
        paths = tuple(sorted(used))
        unknown = unknown_values(used, {item.key for item in applying})
        if unknown:
            return TemplatePreview(used=paths, error=f"unknown value {unknown[0]}", values=written)
        if values is None:
            return TemplatePreview(used=paths)
        try:
            rendered = " ".join(self._templates.render(template, values).split())
        except MissingValueError as missing:
            return TemplatePreview(used=paths, missing=missing.path, values=written)
        except ValidationError as invalid:
            return TemplatePreview(used=paths, error=str(invalid), values=written)
        return TemplatePreview(used=paths, rendered=rendered, values=written)

    def _written(self, values: Mapping[str, TemplateValue]) -> dict[str, str]:
        """Each of a document's values as a template writes it."""
        written: dict[str, str] = {}
        for path, template in writing_templates(values).items():
            try:
                written[path] = self._templates.render(template, values)
            except (MissingValueError, ValidationError):
                continue
        return written

    async def change(self, actor: UserId, id: RuleId, definition: RuleDefinition) -> Rule:
        """A new version; the scope stays."""
        async with self._uow() as uow:
            user = await load_actor(uow, actor)
            rule = await _managed_rule(uow, user, id)
            rule.change(definition, user.id, self._clock.now())
            await check_references(uow, definition, owner=await _owner(uow, rule))
            await check_templates(uow, self._templates, definition)
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
                await check_templates(uow, self._templates, rule.definition)
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
    conditions and values fit their field, tags are of the scope their place asks for (groups
    in the conditions on groups, topics in those on the topic and in "set topic", document
    tags elsewhere; ValidationError), and the `owner` of a user rule may file into its space
    (PermissionDeniedError; checked again whenever it runs)."""
    references = definition.references()
    for contact in references.contacts:
        await uow.contacts.get(contact)
    for document_type in references.document_types:
        await uow.document_types.get(document_type)
    if references.tags:
        tags = {tag.id: tag for tag in await uow.tags.list_all()}
        for id in references.tags:
            if id not in tags:
                raise NotFoundError("tag", id)
        for id, scope in references.tag_scopes:
            if tags[id].scope is not scope:
                raise ValidationError(
                    f"'{tags[id].name}' is a tag of scope {tags[id].scope}, where a tag of "
                    f"scope {scope} belongs"
                )
    if references.fields:
        fields = {field.id: field for field in await uow.fields.list_all()}
        for field_id in references.fields:
            if field_id not in fields:
                raise NotFoundError("field", field_id)
        check_fields(definition, fields)
    for space_id in references.spaces:
        space = await uow.spaces.find(space_id)
        access = None if space is None or owner is None else space_access(owner, space)
        if space is None or access is None:
            raise NotFoundError("space", space_id)
        if not access.can_write:
            raise SpaceWriteDeniedError(space.id, f"'{space.name}'")


async def check_templates(
    uow: UnitOfWork, templates: TemplateRenderer, definition: RuleDefinition
) -> None:
    """The templates of the actions (title, text field, review reason) are valid and use
    values a template has (ValidationError naming the action)."""
    fields = await uow.fields.list_all()
    keys = {field.key for field in fields}
    for index, template in action_templates(definition, fields):
        try:
            used = templates.check(template)
        except ValidationError as error:
            raise ValidationError(f"actions[{index}]: {error}") from None
        unknown = unknown_values(used, keys)
        if unknown:
            known = ", ".join(name for name in NAMES if name != FIELD)
            raise ValidationError(
                f"actions[{index}]: unknown value {unknown[0]}; templates know {known} and "
                "field.<key> of an existing field"
            )


def action_templates(
    definition: RuleDefinition, fields: Iterable[FieldDefinition]
) -> list[tuple[int, str]]:
    """The templates among the actions, with their place: a title, the value of a text field,
    a review's reason."""
    texts = {field.id for field in fields if field.data_type is FieldType.TEXT}
    found: list[tuple[int, str]] = []
    for index, action in enumerate(definition.actions):
        match action:
            case SetTitle(template=template) | ForceReview(reason=template):
                found.append((index, template))
            case SetField(field_id=field, value=str() as value) if field in texts:
                found.append((index, value))
    return found


def uses_field(
    templates: TemplateRenderer,
    definition: RuleDefinition,
    fields: Iterable[FieldDefinition],
    key: str,
) -> bool:
    """Whether a template of the definition uses the field with `key`."""
    return any(
        field_used(templates.check(template), key)
        for _, template in action_templates(definition, fields)
    )


def renamed_field(
    definition: RuleDefinition, fields: Iterable[FieldDefinition], old: str, new: str
) -> RuleDefinition:
    """The definition with `field.<old>` written `field.<new>` in its templates."""
    texts = {field.id for field in fields if field.data_type is FieldType.TEXT}
    actions = []
    for action in definition.actions:
        match action:
            case SetTitle(template=template):
                action = replace(action, template=rename_field_key(template, old, new))
            case ForceReview(reason=reason):
                action = replace(action, reason=rename_field_key(reason, old, new))
            case SetField(field_id=field, value=str() as value) if field in texts:
                action = replace(action, value=rename_field_key(value, old, new))
        actions.append(action)
    return replace(definition, actions=tuple(actions))


async def visible_rule(uow: UnitOfWork, user: User, id: RuleId) -> Rule:
    """Global rules and the caller's own; all rules for admins. Includes deleted rules."""
    rule = await uow.rules.find(id)
    if rule is None or not (
        rule.scope is RuleScope.GLOBAL or rule.owner_id == user.id or user.is_active_admin
    ):
        raise NotFoundError("rule", id)
    return rule


async def _managed_rule(uow: UnitOfWork, user: User, id: RuleId) -> Rule:
    """The rule, if the user may change, switch or delete it; the rule of a title pattern
    nobody does (ConflictError): it follows its document type."""
    rule = await visible_rule(uow, user, id)
    if rule.deleted_at is not None:
        raise NotFoundError("rule", id)
    if rule.managed_by is not None:
        document_type = await uow.document_types.find(rule.managed_by)
        name = rule.definition.name if document_type is None else document_type.name
        raise ConflictError(
            f"the rule of the title pattern of '{name}' follows the document type: change "
            "the pattern there"
        )
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


async def document_values(
    uow: UnitOfWork,
    document: Document,
    document_type: DocumentTypeId | None,
    fields: Iterable[FieldDefinition],
) -> dict[str, TemplateValue]:
    """The values a template sees of `document` as it is (`templates.template_values`)."""
    contact = None if document.contact_id is None else await uow.contacts.find(document.contact_id)
    kind = None if document_type is None else await uow.document_types.find(document_type)
    space = await uow.spaces.find(document.space_id)
    owner = await uow.users.find(document.owner_id)
    names = {tag.id: tag.name for tag in await uow.tags.list_all()}
    tags = [names[id] for id in document.tag_ids if id in names]
    return template_values(
        document,
        contact=None if contact is None else contact.name,
        document_type=None if kind is None else kind.name,
        space=None if space is None else space.name,
        owner=None if owner is None else owner.username,
        tags=tags,
        document_date=document.document_date,
        fields=[(item, document.fields.get(item.id)) for item in fields],
        contact_groups=[names[id] for id in (contact.group_ids if contact else ()) if id in names],
        document_type_groups=[names[id] for id in (kind.group_ids if kind else ()) if id in names],
        topic=names.get(document.topic_id) if document.topic_id is not None else None,
    )
