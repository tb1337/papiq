"""Rules: data that tags, files and checks documents.

A rule has a scope, triggers, a tree of conditions and actions:

- Scope: a global rule is made by an admin and acts on every document, but only on tags,
  attributes and reviews (nothing that changes who sees a document). A user rule is made by any
  user and acts on that user's own documents with all actions; it files only into drawers its
  owner may write to.
- Triggers: a document arriving (`ingest`, the pipeline step `apply_rules`) and a document being
  changed by a person (`change`).
- Conditions: groups of `all` (and) or `any` (or), each may be negated; a condition is field,
  operator and value.
- Actions: set drawer, contact, type, title or an attribute, add or remove tags, force a review.

The definition (name, priority, triggers, conditions, actions) is versioned: every change makes
a new version; old versions stay readable so the processing log can refer to them. Enabling and
disabling are state, not content, and make no version.

Definitions are stored as JSON (`definition_to_json`, `definition_from_json`). Values in
conditions and actions keep their JSON form (attribute values as `attribute_to_json` writes
them) and are read with the attribute definitions where needed (`check_attributes`).
"""

import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Literal, Self
from uuid import UUID

from papiq.core.domain.attributes import AttributeDefinition, AttributeType
from papiq.core.domain.classification import attribute_from_json
from papiq.core.domain.documents import Channel
from papiq.core.domain.errors import ValidationError
from papiq.core.domain.ids import (
    AttributeId,
    ContactId,
    DocumentId,
    DocumentTypeId,
    DrawerId,
    RuleApplicationId,
    RuleId,
    TagId,
    UserId,
    new_id,
)
from papiq.core.domain.json_value import JsonObject, JsonValue
from papiq.core.domain.validation import require_name, require_utc

DEFAULT_PRIORITY = 100
MIN_PRIORITY = 0
MAX_PRIORITY = 1000
MAX_DEPTH = 5
MAX_CONDITIONS = 50
MAX_ACTIONS = 20
MAX_NAME = 200
MAX_TEXT = 500
MAX_PATTERN = 200
MAX_LIST = 100
TITLE_PLACEHOLDERS = ("contact", "document_type", "document_date", "filename")
_PLACEHOLDER = re.compile(r"\{([^{}]*)\}")


class RuleScope(StrEnum):
    GLOBAL = "global"
    USER = "user"


class Trigger(StrEnum):
    INGEST = "ingest"  # the pipeline step `apply_rules`
    CHANGE = "change"  # a person changed the document's metadata


class ConditionField(StrEnum):
    CONTACT = "contact"
    DOCUMENT_TYPE = "document_type"
    TAGS = "tags"
    CHANNEL = "channel"
    TEXT = "text"
    ATTRIBUTE = "attribute"
    DOCUMENT_DATE = "document_date"


class Operator(StrEnum):
    IS = "is"
    IN = "in"  # is one of; for tags: has one of
    CONTAINS = "contains"  # for tags: has this tag
    MATCHES = "matches"  # regular expression
    GT = "gt"
    LT = "lt"
    PRESENT = "present"
    MISSING = "missing"


type GroupMode = Literal["all", "any"]

_REFERENCE_OPERATORS = frozenset({Operator.IS, Operator.IN, Operator.PRESENT, Operator.MISSING})
_FIELD_OPERATORS: dict[ConditionField, frozenset[Operator]] = {
    ConditionField.CONTACT: _REFERENCE_OPERATORS,
    ConditionField.DOCUMENT_TYPE: _REFERENCE_OPERATORS,
    ConditionField.TAGS: frozenset(
        {Operator.CONTAINS, Operator.IN, Operator.PRESENT, Operator.MISSING}
    ),
    ConditionField.CHANNEL: frozenset({Operator.IS, Operator.IN}),
    ConditionField.TEXT: frozenset({Operator.CONTAINS, Operator.MATCHES}),
    ConditionField.DOCUMENT_DATE: frozenset(
        {Operator.IS, Operator.GT, Operator.LT, Operator.PRESENT, Operator.MISSING}
    ),
}
_ID_FIELDS = frozenset({ConditionField.CONTACT, ConditionField.DOCUMENT_TYPE, ConditionField.TAGS})
_ORDERED = frozenset({Operator.IS, Operator.GT, Operator.LT, Operator.PRESENT, Operator.MISSING})
ATTRIBUTE_OPERATORS: dict[AttributeType, frozenset[Operator]] = {
    AttributeType.TEXT: frozenset(
        {
            Operator.IS,
            Operator.IN,
            Operator.CONTAINS,
            Operator.MATCHES,
            Operator.PRESENT,
            Operator.MISSING,
        }
    ),
    AttributeType.LINK: frozenset(
        {
            Operator.IS,
            Operator.IN,
            Operator.CONTAINS,
            Operator.MATCHES,
            Operator.PRESENT,
            Operator.MISSING,
        }
    ),
    AttributeType.NUMBER: _ORDERED,
    AttributeType.AMOUNT: _ORDERED,
    AttributeType.DATE: _ORDERED,
    AttributeType.BOOLEAN: frozenset({Operator.IS, Operator.PRESENT, Operator.MISSING}),
    AttributeType.CHOICE: frozenset({Operator.IS, Operator.IN, Operator.PRESENT, Operator.MISSING}),
}


# --- conditions -------------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class Condition:
    """Field, operator and value. `value` is None for `present` and `missing`, a list for
    `in`, otherwise one value. `attribute_id` names the attribute of an `attribute` condition;
    `case_sensitive` applies to `matches` only."""

    field: ConditionField
    op: Operator
    value: JsonValue = None
    attribute_id: AttributeId | None = None
    case_sensitive: bool = False

    def __post_init__(self) -> None:
        if (self.field is ConditionField.ATTRIBUTE) != (self.attribute_id is not None):
            raise ValidationError("attribute_id is given exactly for attribute conditions")
        if (
            self.field is not ConditionField.ATTRIBUTE
            and self.op not in _FIELD_OPERATORS[self.field]
        ):
            raise ValidationError(f"operator '{self.op}' does not apply to {self.field}")
        if self.case_sensitive and self.op is not Operator.MATCHES:
            raise ValidationError("case_sensitive applies to 'matches' only")
        if self.op in (Operator.PRESENT, Operator.MISSING):
            if self.value is not None:
                raise ValidationError(f"'{self.op}' takes no value")
            return
        if self.op is Operator.IN:
            if not isinstance(self.value, list) or not self.value:
                raise ValidationError("'in' takes a non-empty list")
            if len(self.value) > MAX_LIST:
                raise ValidationError(f"'in' takes at most {MAX_LIST} values")
            items = self.value
        else:
            if self.value is None or isinstance(self.value, list):
                raise ValidationError(f"'{self.op}' takes one value")
            items = [self.value]
        for item in items:
            self._check_value(item)
        if self.field in _ID_FIELDS:
            # One spelling of every id, so conditions compare ids as text.
            ids = [str(_uuid(item, self.field.value)) for item in items]
            object.__setattr__(self, "value", ids if self.op is Operator.IN else ids[0])

    def _check_value(self, value: JsonValue) -> None:
        match self.field:
            case ConditionField.CONTACT | ConditionField.DOCUMENT_TYPE | ConditionField.TAGS:
                _uuid(value, self.field.value)
            case ConditionField.CHANNEL:
                if not isinstance(value, str) or value not in {
                    channel.value for channel in Channel
                }:
                    raise ValidationError(f"unknown channel {value!r}")
            case ConditionField.TEXT:
                _text(value, MAX_PATTERN if self.op is Operator.MATCHES else MAX_TEXT)
                if self.op is Operator.MATCHES:
                    _pattern(value)
            case ConditionField.DOCUMENT_DATE:
                _date(value)
            case ConditionField.ATTRIBUTE:
                # Checked against the definition by `check_attributes`; here only patterns.
                if self.op is Operator.MATCHES:
                    _text(value, MAX_PATTERN)
                    _pattern(value)
                elif self.op is Operator.CONTAINS:
                    _text(value, MAX_TEXT)

    @property
    def values(self) -> list[JsonValue]:
        if self.op is Operator.IN:
            assert isinstance(self.value, list)
            return list(self.value)
        return [] if self.value is None else [self.value]


@dataclass(frozen=True, kw_only=True)
class Group:
    """`all` (and) or `any` (or) of its items; `negate` turns the result around."""

    mode: GroupMode
    items: tuple["Condition | Group", ...]
    negate: bool = False

    def __post_init__(self) -> None:
        if self.mode not in ("all", "any"):
            raise ValidationError(f"a group is 'all' or 'any', not {self.mode!r}")
        if not self.items:
            raise ValidationError("a group needs at least one condition")

    def conditions(self) -> list[Condition]:
        """All conditions of the tree, depth first."""
        found: list[Condition] = []
        for item in self.items:
            if isinstance(item, Group):
                found.extend(item.conditions())
            else:
                found.append(item)
        return found

    @property
    def depth(self) -> int:
        return 1 + max((item.depth for item in self.items if isinstance(item, Group)), default=0)


# --- actions ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class SetDrawer:
    drawer_id: DrawerId
    type: Literal["set_drawer"] = "set_drawer"


@dataclass(frozen=True)
class SetContact:
    contact_id: ContactId
    type: Literal["set_contact"] = "set_contact"


@dataclass(frozen=True)
class SetDocumentType:
    document_type_id: DocumentTypeId
    type: Literal["set_document_type"] = "set_document_type"


@dataclass(frozen=True)
class SetTitle:
    """The title from a template; placeholders `{contact}`, `{document_type}`,
    `{document_date}` (ISO) and `{filename}` (the original's name without extension)."""

    template: str
    type: Literal["set_title"] = "set_title"

    def __post_init__(self) -> None:
        _text(self.template, MAX_TEXT)
        for name in _PLACEHOLDER.findall(self.template):
            if name not in TITLE_PLACEHOLDERS:
                raise ValidationError(
                    f"unknown placeholder {{{name}}}; known: "
                    + ", ".join(f"{{{item}}}" for item in TITLE_PLACEHOLDERS)
                )


@dataclass(frozen=True)
class AddTags:
    tag_ids: frozenset[TagId]
    type: Literal["add_tags"] = "add_tags"

    def __post_init__(self) -> None:
        _tag_list(self.tag_ids)


@dataclass(frozen=True)
class RemoveTags:
    tag_ids: frozenset[TagId]
    type: Literal["remove_tags"] = "remove_tags"

    def __post_init__(self) -> None:
        _tag_list(self.tag_ids)


@dataclass(frozen=True)
class SetAttribute:
    """`value` in the JSON form of the attribute's type (`attribute_to_json`)."""

    attribute_id: AttributeId
    value: JsonValue
    type: Literal["set_attribute"] = "set_attribute"

    def __post_init__(self) -> None:
        if self.value is None:
            raise ValidationError("set_attribute needs a value")


@dataclass(frozen=True)
class ForceReview:
    """The document waits in the inbox with this reason (when it arrives)."""

    reason: str
    type: Literal["force_review"] = "force_review"

    def __post_init__(self) -> None:
        _text(self.reason, MAX_TEXT)


type Action = (
    SetDrawer
    | SetContact
    | SetDocumentType
    | SetTitle
    | AddTags
    | RemoveTags
    | SetAttribute
    | ForceReview
)

GLOBAL_ACTIONS: tuple[type, ...] = (AddTags, RemoveTags, SetAttribute, ForceReview)
"""What a global rule may do: nothing that changes who sees a document."""


# --- definition -------------------------------------------------------------------------------


@dataclass(frozen=True)
class References:
    """What a definition refers to; the caller checks that it exists."""

    contacts: frozenset[ContactId] = frozenset()
    document_types: frozenset[DocumentTypeId] = frozenset()
    tags: frozenset[TagId] = frozenset()
    attributes: frozenset[AttributeId] = frozenset()
    drawers: frozenset[DrawerId] = frozenset()


@dataclass(frozen=True, kw_only=True)
class RuleDefinition:
    """The versioned content of a rule. Checked here for everything that needs no stored
    data; `check_scope` and `check_attributes` do the rest."""

    name: str
    conditions: Group
    actions: tuple[Action, ...]
    priority: int = DEFAULT_PRIORITY
    triggers: frozenset[Trigger] = frozenset(Trigger)

    def __post_init__(self) -> None:
        name = require_name(self.name, "rule name")
        if len(name) > MAX_NAME:
            raise ValidationError(f"the rule name has more than {MAX_NAME} characters")
        object.__setattr__(self, "name", name)
        if (
            isinstance(self.priority, bool)
            or not isinstance(self.priority, int)
            or not MIN_PRIORITY <= self.priority <= MAX_PRIORITY
        ):
            raise ValidationError(
                f"priority must be a whole number from {MIN_PRIORITY} to {MAX_PRIORITY}"
            )
        if not self.triggers:
            raise ValidationError("a rule needs at least one trigger")
        if self.conditions.depth > MAX_DEPTH:
            raise ValidationError(f"conditions may be nested at most {MAX_DEPTH} deep")
        if len(self.conditions.conditions()) > MAX_CONDITIONS:
            raise ValidationError(f"a rule has at most {MAX_CONDITIONS} conditions")
        if not self.actions:
            raise ValidationError("a rule needs at least one action")
        if len(self.actions) > MAX_ACTIONS:
            raise ValidationError(f"a rule has at most {MAX_ACTIONS} actions")
        seen: set[str] = set()
        for index, action in enumerate(self.actions):
            key = _single_key(action)
            if key in seen:
                raise ValidationError(
                    f"actions[{index}]: an action sets the same field twice: {key}"
                )
            if key is not None:
                seen.add(key)

    def references(self) -> References:
        contacts: set[ContactId] = set()
        types: set[DocumentTypeId] = set()
        tags: set[TagId] = set()
        attributes: set[AttributeId] = set()
        drawers: set[DrawerId] = set()
        for condition in self.conditions.conditions():
            match condition.field:
                case ConditionField.CONTACT:
                    contacts.update(ContactId(id) for id in _ids(condition))
                case ConditionField.DOCUMENT_TYPE:
                    types.update(DocumentTypeId(id) for id in _ids(condition))
                case ConditionField.TAGS:
                    tags.update(TagId(id) for id in _ids(condition))
                case ConditionField.ATTRIBUTE:
                    assert condition.attribute_id is not None
                    attributes.add(condition.attribute_id)
        for action in self.actions:
            match action:
                case SetDrawer(drawer_id=drawer):
                    drawers.add(drawer)
                case SetContact(contact_id=contact):
                    contacts.add(contact)
                case SetDocumentType(document_type_id=document_type):
                    types.add(document_type)
                case AddTags(tag_ids=ids) | RemoveTags(tag_ids=ids):
                    tags.update(ids)
                case SetAttribute(attribute_id=attribute):
                    attributes.add(attribute)
        return References(
            frozenset(contacts),
            frozenset(types),
            frozenset(tags),
            frozenset(attributes),
            frozenset(drawers),
        )

    @property
    def uses_text(self) -> bool:
        return any(
            condition.field is ConditionField.TEXT for condition in self.conditions.conditions()
        )

    def patterns(self) -> set[tuple[str, str, bool]]:
        """The regular expressions of the conditions: target (`text` or an attribute id),
        pattern and case setting."""
        return {
            (
                "text" if condition.attribute_id is None else str(condition.attribute_id),
                str(condition.value),
                condition.case_sensitive,
            )
            for condition in self.conditions.conditions()
            if condition.op is Operator.MATCHES
        }


def _ids(condition: Condition) -> list[UUID]:
    return [UUID(str(value)) for value in condition.values]


def _single_key(action: Action) -> str | None:
    """The field an action sets alone; two such actions in one rule contradict each other."""
    match action:
        case SetAttribute(attribute_id=attribute):
            return f"attribute:{attribute}"
        case AddTags() | RemoveTags() | ForceReview():
            return None
    return action.type


def check_scope(definition: RuleDefinition, scope: RuleScope) -> None:
    """A global rule may only tag, set attributes and force a review."""
    if scope is RuleScope.GLOBAL:
        for index, action in enumerate(definition.actions):
            if not isinstance(action, GLOBAL_ACTIONS):
                raise ValidationError(
                    f"actions[{index}]: a global rule cannot {action.type}: global rules only "
                    "add or remove tags, set attributes and force a review"
                )


def check_attributes(
    definition: RuleDefinition, definitions: Mapping[AttributeId, AttributeDefinition]
) -> None:
    """Conditions and actions on attributes fit the attribute's data type. ValidationError
    otherwise, naming the place; a missing attribute is a ValidationError too."""
    for place, condition in condition_places(definition.conditions):
        if condition.field is not ConditionField.ATTRIBUTE:
            continue
        assert condition.attribute_id is not None
        with _at(place):
            attribute = _attribute(definitions, condition.attribute_id)
            if condition.op not in ATTRIBUTE_OPERATORS[attribute.data_type]:
                raise ValidationError(
                    f"operator '{condition.op}' does not apply to attribute '{attribute.name}' "
                    f"({attribute.data_type})"
                )
            if condition.op in (Operator.CONTAINS, Operator.MATCHES):
                continue
            for value in condition.values:
                attribute_from_json(attribute, value)
    for index, action in enumerate(definition.actions):
        if isinstance(action, SetAttribute):
            with _at(f"actions[{index}]"):
                attribute_from_json(_attribute(definitions, action.attribute_id), action.value)


def condition_places(group: Group, where: str = "conditions") -> Iterator[tuple[str, Condition]]:
    """The conditions of the tree with their place in the JSON form, depth first."""
    for index, item in enumerate(group.items):
        place = f"{where}.{group.mode}[{index}]"
        if isinstance(item, Group):
            yield from condition_places(item, place)
        else:
            yield place, item


@contextmanager
def _at(place: str) -> Iterator[None]:
    """Prefix the message of a ValidationError with `place`."""
    try:
        yield
    except ValidationError as error:
        raise ValidationError(f"{place}: {error}") from None


def _attribute(
    definitions: Mapping[AttributeId, AttributeDefinition], id: AttributeId
) -> AttributeDefinition:
    try:
        return definitions[id]
    except KeyError:
        raise ValidationError(f"attribute {id} does not exist") from None


# --- rule -------------------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class RuleVersion:
    rule_id: RuleId
    number: int
    definition: RuleDefinition
    created_at: datetime
    created_by: UserId | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "created_at", require_utc(self.created_at, "created_at"))
        if self.number < 1:
            raise ValidationError("rule versions start at 1")


@dataclass(kw_only=True)
class Rule:
    """A rule with its current version. `owner_id` is None exactly for global rules.

    `disabled_reason` says why the rule was disabled automatically (e.g. a contact it refers to
    was deleted); None if a person disabled it or it is enabled. A deleted rule stays stored
    for its versions; it never runs and is not listed."""

    id: RuleId
    scope: RuleScope
    owner_id: UserId | None
    current: RuleVersion
    created_at: datetime
    updated_at: datetime
    enabled: bool = True
    disabled_reason: str | None = None
    deleted_at: datetime | None = None
    version: int = 1

    def __post_init__(self) -> None:
        if (self.scope is RuleScope.GLOBAL) != (self.owner_id is None):
            raise ValidationError("global rules have no owner, user rules have one")
        if self.current.rule_id != self.id:
            raise ValidationError("the current version belongs to another rule")
        check_scope(self.current.definition, self.scope)
        self.created_at = require_utc(self.created_at, "created_at")
        self.updated_at = require_utc(self.updated_at, "updated_at")

    @classmethod
    def create(
        cls,
        *,
        scope: RuleScope,
        owner_id: UserId | None,
        definition: RuleDefinition,
        by: UserId,
        now: datetime,
    ) -> Self:
        id = RuleId(new_id())
        return cls(
            id=id,
            scope=scope,
            owner_id=owner_id,
            current=RuleVersion(
                rule_id=id, number=1, definition=definition, created_at=now, created_by=by
            ),
            created_at=now,
            updated_at=now,
        )

    @property
    def definition(self) -> RuleDefinition:
        return self.current.definition

    @property
    def is_active(self) -> bool:
        return self.enabled and self.deleted_at is None

    def change(self, definition: RuleDefinition, by: UserId, now: datetime) -> RuleVersion:
        """A new version with `definition`."""
        self._check_not_deleted()
        check_scope(definition, self.scope)
        self.current = RuleVersion(
            rule_id=self.id,
            number=self.current.number + 1,
            definition=definition,
            created_at=now,
            created_by=by,
        )
        self.updated_at = require_utc(now, "updated_at")
        return self.current

    def enable(self, now: datetime) -> None:
        self._check_not_deleted()
        self.enabled, self.disabled_reason = True, None
        self.updated_at = require_utc(now, "updated_at")

    def disable(self, now: datetime, reason: str | None = None) -> None:
        self._check_not_deleted()
        self.enabled, self.disabled_reason = False, reason
        self.updated_at = require_utc(now, "updated_at")

    def delete(self, now: datetime) -> None:
        self._check_not_deleted()
        self.enabled = False
        self.deleted_at = self.updated_at = require_utc(now, "deleted_at")

    def _check_not_deleted(self) -> None:
        if self.deleted_at is not None:
            raise ValidationError(f"rule {self.id} is deleted")

    @property
    def order(self) -> tuple[int, int, datetime, RuleId]:
        """Sort key: higher priority first, then global before user rules, then older first."""
        return (
            -self.definition.priority,
            0 if self.scope is RuleScope.GLOBAL else 1,
            self.created_at,
            self.id,
        )


# --- applying a rule to existing documents ----------------------------------------------------


class ApplicationStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


@dataclass(kw_only=True)
class RuleApplication:
    """A person applies one version of a rule to documents they selected (in the background).

    `accept_conflicts`: documents where the rule's value replaces a different value the
    document has (a deliberate decision, no review). `position`: how many of `documents` are
    done. `skipped`: document id and reason."""

    id: RuleApplicationId
    rule_id: RuleId
    rule_version: int
    user_id: UserId
    documents: tuple[DocumentId, ...]
    accept_conflicts: frozenset[DocumentId] = frozenset()
    status: ApplicationStatus = ApplicationStatus.QUEUED
    position: int = 0
    applied: int = 0
    unchanged: int = 0
    skipped: list[tuple[DocumentId, str]] = field(default_factory=list)
    error: str | None = None
    created_at: datetime
    finished_at: datetime | None = None
    version: int = 1

    def __post_init__(self) -> None:
        if not self.documents:
            raise ValidationError("select at least one document")
        if len(set(self.documents)) != len(self.documents):
            raise ValidationError("a document is selected twice")
        if not self.accept_conflicts <= set(self.documents):
            raise ValidationError("accepted conflicts must be among the selected documents")
        self.created_at = require_utc(self.created_at, "created_at")

    @classmethod
    def create(
        cls,
        *,
        rule_id: RuleId,
        rule_version: int,
        user_id: UserId,
        documents: tuple[DocumentId, ...],
        accept_conflicts: frozenset[DocumentId],
        now: datetime,
    ) -> Self:
        return cls(
            id=RuleApplicationId(new_id()),
            rule_id=rule_id,
            rule_version=rule_version,
            user_id=user_id,
            documents=documents,
            accept_conflicts=accept_conflicts,
            created_at=now,
        )

    @property
    def remaining(self) -> tuple[DocumentId, ...]:
        return self.documents[self.position :]

    def record(self, document: DocumentId, outcome: Literal["applied", "unchanged"] | str) -> None:
        """One more document done: applied, unchanged, or skipped with a reason."""
        if outcome == "applied":
            self.applied += 1
        elif outcome == "unchanged":
            self.unchanged += 1
        else:
            self.skipped.append((document, outcome))
        self.position += 1
        self.status = ApplicationStatus.RUNNING

    def finish(self, now: datetime, error: str | None = None) -> None:
        self.status = ApplicationStatus.FAILED if error else ApplicationStatus.DONE
        self.error = error
        self.finished_at = require_utc(now, "finished_at")


# --- JSON -------------------------------------------------------------------------------------


def definition_to_json(definition: RuleDefinition) -> JsonObject:
    return {
        "name": definition.name,
        "priority": definition.priority,
        "triggers": _strings(sorted(trigger.value for trigger in definition.triggers)),
        "conditions": group_to_json(definition.conditions),
        "actions": [action_to_json(action) for action in definition.actions],
    }


def group_to_json(group: Group) -> JsonObject:
    data: JsonObject = {
        group.mode: [
            group_to_json(item) if isinstance(item, Group) else condition_to_json(item)
            for item in group.items
        ]
    }
    if group.negate:
        data["not"] = True
    return data


def condition_to_json(condition: Condition) -> JsonObject:
    data: JsonObject = {"field": condition.field.value, "op": condition.op.value}
    if condition.attribute_id is not None:
        data["attribute_id"] = str(condition.attribute_id)
    if condition.value is not None:
        data["value"] = condition.value
    if condition.case_sensitive:
        data["case_sensitive"] = True
    return data


def action_to_json(action: Action) -> JsonObject:
    match action:
        case SetDrawer(drawer_id=drawer):
            return {"type": action.type, "drawer_id": str(drawer)}
        case SetContact(contact_id=contact):
            return {"type": action.type, "contact_id": str(contact)}
        case SetDocumentType(document_type_id=document_type):
            return {"type": action.type, "document_type_id": str(document_type)}
        case SetTitle(template=template):
            return {"type": action.type, "template": template}
        case AddTags(tag_ids=tags) | RemoveTags(tag_ids=tags):
            return {"type": action.type, "tag_ids": _strings(sorted(str(tag) for tag in tags))}
        case SetAttribute(attribute_id=attribute, value=value):
            return {"type": action.type, "attribute_id": str(attribute), "value": value}
        case ForceReview(reason=reason):
            return {"type": action.type, "reason": reason}


def definition_from_json(data: JsonValue) -> RuleDefinition:
    """ValidationError, naming the place, if `data` is no valid definition."""
    object_ = _object(data, "rule")
    _only(object_, {"name", "priority", "triggers", "conditions", "actions"}, "rule")
    triggers = object_.get("triggers", [trigger.value for trigger in Trigger])
    if not isinstance(triggers, list):
        raise ValidationError("triggers: expected a list")
    actions = object_.get("actions")
    if not isinstance(actions, list):
        raise ValidationError("actions: expected a list")
    priority = object_.get("priority", DEFAULT_PRIORITY)
    if not isinstance(priority, int):
        raise ValidationError("priority: expected a whole number")
    name = object_.get("name")
    if not isinstance(name, str):
        raise ValidationError("name: expected text")
    return RuleDefinition(
        name=name,
        priority=priority,
        triggers=frozenset(_enum(Trigger, item, "triggers") for item in triggers),
        conditions=group_from_json(object_.get("conditions"), "conditions"),
        actions=tuple(
            action_from_json(item, f"actions[{index}]") for index, item in enumerate(actions)
        ),
    )


def group_from_json(data: JsonValue, where: str = "conditions") -> Group:
    object_ = _object(data, where)
    modes = [mode for mode in ("all", "any") if mode in object_]
    if len(modes) != 1:
        raise ValidationError(f"{where}: a group has exactly one of 'all' and 'any'")
    mode: GroupMode = "all" if modes[0] == "all" else "any"
    _only(object_, {mode, "not"}, where)
    negate = object_.get("not", False)
    if not isinstance(negate, bool):
        raise ValidationError(f"{where}.not: expected true or false")
    items = object_[mode]
    if not isinstance(items, list):
        raise ValidationError(f"{where}.{mode}: expected a list")
    parsed: list[Condition | Group] = []
    for index, item in enumerate(items):
        place = f"{where}.{mode}[{index}]"
        if isinstance(item, dict) and ("all" in item or "any" in item):
            parsed.append(group_from_json(item, place))
        else:
            parsed.append(condition_from_json(item, place))
    try:
        return Group(mode=mode, items=tuple(parsed), negate=negate)
    except ValidationError as error:
        raise ValidationError(f"{where}: {error}") from None


def condition_from_json(data: JsonValue, where: str = "condition") -> Condition:
    object_ = _object(data, where)
    _only(object_, {"field", "op", "value", "attribute_id", "case_sensitive"}, where)
    attribute = object_.get("attribute_id")
    case_sensitive = object_.get("case_sensitive", False)
    if not isinstance(case_sensitive, bool):
        raise ValidationError(f"{where}.case_sensitive: expected true or false")
    try:
        return Condition(
            field=_enum(ConditionField, object_.get("field"), f"{where}.field"),
            op=_enum(Operator, object_.get("op"), f"{where}.op"),
            value=object_.get("value"),
            attribute_id=None if attribute is None else AttributeId(_uuid(attribute, "attribute")),
            case_sensitive=case_sensitive,
        )
    except ValidationError as error:
        raise ValidationError(f"{where}: {error}") from None


def action_from_json(data: JsonValue, where: str = "action") -> Action:
    object_ = _object(data, where)
    kind = object_.get("type")
    try:
        match kind:
            case "set_drawer":
                _only(object_, {"type", "drawer_id"}, where)
                return SetDrawer(DrawerId(_uuid(object_.get("drawer_id"), "drawer_id")))
            case "set_contact":
                _only(object_, {"type", "contact_id"}, where)
                return SetContact(ContactId(_uuid(object_.get("contact_id"), "contact_id")))
            case "set_document_type":
                _only(object_, {"type", "document_type_id"}, where)
                return SetDocumentType(
                    DocumentTypeId(_uuid(object_.get("document_type_id"), "document_type_id"))
                )
            case "set_title":
                _only(object_, {"type", "template"}, where)
                template = object_.get("template")
                if not isinstance(template, str):
                    raise ValidationError("template: expected text")
                return SetTitle(template)
            case "add_tags" | "remove_tags":
                _only(object_, {"type", "tag_ids"}, where)
                ids = object_.get("tag_ids")
                if not isinstance(ids, list):
                    raise ValidationError("tag_ids: expected a list")
                tags = frozenset(TagId(_uuid(item, "tag_ids")) for item in ids)
                return AddTags(tags) if kind == "add_tags" else RemoveTags(tags)
            case "set_attribute":
                _only(object_, {"type", "attribute_id", "value"}, where)
                return SetAttribute(
                    AttributeId(_uuid(object_.get("attribute_id"), "attribute_id")),
                    object_.get("value"),
                )
            case "force_review":
                _only(object_, {"type", "reason"}, where)
                reason = object_.get("reason")
                if not isinstance(reason, str):
                    raise ValidationError("reason: expected text")
                return ForceReview(reason)
    except ValidationError as error:
        raise ValidationError(f"{where}: {error}") from None
    raise ValidationError(f"{where}.type: unknown action {kind!r}")


# --- helpers ----------------------------------------------------------------------------------


def _strings(items: list[str]) -> list[JsonValue]:
    return list(items)


def _object(data: JsonValue, where: str) -> dict[str, JsonValue]:
    if not isinstance(data, dict):
        raise ValidationError(f"{where}: expected an object")
    return data


def _only(data: Mapping[str, JsonValue], allowed: set[str], where: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ValidationError(f"{where}: unknown key '{unknown[0]}'")


def _enum[E: StrEnum](kind: type[E], value: JsonValue, where: str) -> E:
    try:
        return kind(str(value))
    except ValueError:
        raise ValidationError(f"{where}: unknown value {value!r}") from None


def _uuid(value: JsonValue, what: str) -> UUID:
    try:
        if isinstance(value, str):
            return UUID(value)
    except ValueError:
        pass
    raise ValidationError(f"{what}: {value!r} is no id")


def _text(value: JsonValue, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"expected non-empty text, got {value!r}")
    if len(value) > limit:
        raise ValidationError(f"text longer than {limit} characters")
    return value


def _pattern(value: JsonValue) -> None:
    try:
        re.compile(str(value))
    except re.error as error:
        raise ValidationError(f"invalid regular expression {value!r}: {error}") from None


def _date(value: JsonValue) -> date:
    try:
        if isinstance(value, str):
            return date.fromisoformat(value)
    except ValueError:
        pass
    raise ValidationError(f"{value!r} is no date (YYYY-MM-DD)")


def _tag_list(tags: frozenset[TagId]) -> None:
    if not tags:
        raise ValidationError("give at least one tag")
    if len(tags) > MAX_LIST:
        raise ValidationError(f"at most {MAX_LIST} tags")
