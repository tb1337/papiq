"""Evaluating rules against a document and turning the actions of the matching rules into one
change, with conflicts instead of silent overwrites.

Conditions are evaluated against the state before any rule acts (a rule's action never makes
another rule match in the same run). A condition on a tag, a group or a topic matches the ones
below it too (the document has the tag set and its ancestors; its contact and type have their
groups and theirs; its topic has its ancestors). The result of a condition is three-valued: with
`distrust`, a condition on a contact, type, topic or tag that the model set and no person
confirmed is unknown, and so is one on the groups of such a contact or type. A rule that matches
only with such values does not file into a space that others see (the accepted risk of M5: a
text can name a wrong contact and make it green).

How the actions of all matching rules come together (`plan`):

- A single field (space, contact, type, topic, title, a field) set by one rule, or by several
  to the same value, is set. Several different values are a conflict: nothing is set.
- A topic is set only if the contact the rules leave enables it (otherwise the action is
  skipped and noted); a contact set that does not enable the document's topic clears it,
  unless a person chose that topic: then the contact is overruled.
- A value a person decided (confirming in the inbox, or changing the document) is never
  replaced: the rule is overruled and that is logged.
- A value the model set and no person confirmed is not replaced either: a conflict.
- Tags added and removed by rules are combined; a tag added by one rule and removed by another
  is a conflict. Removing a tag the model set is no conflict (tags are unchecked proposals). Only
  document tags are added (also those set by hand); the result is normalised: a tag above one
  the document keeps is not added, and a parent gives way to its child.
- On arrival (`ingest`) conflicts, refused actions and forced reviews make the node uncertain:
  the document waits in the inbox. On a change by a person (`change`) nothing turns yellow (a
  filed document would disappear for everyone else): what cannot be applied is only reported.
- Applied to existing documents on request (`retroactive`), one rule: a field that has another
  value already is a conflict, applied only where the person accepted it.

Reasons name what they are about as a person reads it: contacts, types, topics, spaces, tags
and fields by their names, a field's value as written (`_Planner._shown`); the ids stay in the
structured parts (values, suggestions, effects). Only something that no longer exists is named
by its id.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal
from enum import StrEnum
from pathlib import PurePath
from uuid import UUID

from papiq.core.domain.classification import (
    CONTACT,
    DOCUMENT_TYPE,
    TAGS,
    TITLE,
    TOPIC,
    FieldCheck,
    field_key,
)
from papiq.core.domain.documents import Channel, Document, DocumentChanges, Unset
from papiq.core.domain.errors import MissingValueError, ValidationError
from papiq.core.domain.evidence import DocumentText, contains, normalise
from papiq.core.domain.fields import (
    FieldDefinition,
    FieldType,
    FieldValue,
    Money,
    Url,
    decimal_text,
    field_from_json,
    field_to_json,
)
from papiq.core.domain.ids import (
    ContactId,
    DocumentTypeId,
    FieldId,
    RuleId,
    SpaceId,
    TagId,
)
from papiq.core.domain.json_value import JsonObject, JsonValue
from papiq.core.domain.master_data import TagScope
from papiq.core.domain.periods import Period
from papiq.core.domain.pipeline import Outcome
from papiq.core.domain.rules import (
    AddTags,
    Condition,
    ConditionField,
    ForceReview,
    Group,
    Operator,
    RemoveTags,
    Rule,
    RuleScope,
    SetContact,
    SetDocumentType,
    SetField,
    SetSpace,
    SetTitle,
    SetTopic,
    condition_value,
)
from papiq.core.domain.tag_tree import TagTree
from papiq.core.domain.templates import Render, TemplateValue, template_values

SPACE = "space"
REVIEW = "review"
TEXT_TARGET = "text"

type Truth = bool | None
"""Three-valued: None is unknown."""


class Mode(StrEnum):
    INGEST = "ingest"
    CHANGE = "change"
    RETROACTIVE = "retroactive"


# --- facts and evaluation ---------------------------------------------------------------------


@dataclass(frozen=True)
class Taxonomy:
    """The tags as trees, the groups of the contacts and types a run looks at (by the
    contact's or type's id), and the topics those contacts enable."""

    tree: TagTree = field(default_factory=lambda: TagTree.of(()))
    groups: Mapping[UUID, frozenset[TagId]] = field(default_factory=dict)
    topics: Mapping[UUID, frozenset[TagId]] = field(default_factory=dict)

    def groups_of(self, id: UUID | None) -> frozenset[TagId]:
        return frozenset() if id is None else self.groups.get(id, frozenset())

    def topics_of(self, contact: UUID | None) -> frozenset[TagId]:
        return frozenset() if contact is None else self.topics.get(contact, frozenset())


@dataclass(frozen=True, kw_only=True)
class Facts:
    """A document's state as conditions see it.

    `patterns`: results of the regular expressions, by (target, pattern, case setting), target
    `text` or a field id; computed beforehand with a time limit. A missing result (no
    text, time out) does not match. `unverified`: the fields `contact`, `document_type` and
    `topic` if they hold a value the model set and no person confirmed; `unverified_tags`
    likewise.
    `taxonomy`: the tag trees; `contact_groups` and `document_type_groups` the groups of the
    contact and the type."""

    contact_id: ContactId | None
    document_type_id: DocumentTypeId | None
    tag_ids: frozenset[TagId]
    channel: Channel
    document_date: date | None
    fields: Mapping[FieldId, FieldValue]
    topic_id: TagId | None = None
    text: DocumentText | None = None
    patterns: Mapping[tuple[str, str, bool], bool] = field(default_factory=dict)
    unverified: frozenset[str] = frozenset()
    unverified_tags: frozenset[TagId] = frozenset()
    taxonomy: Taxonomy = field(default_factory=Taxonomy)
    contact_groups: frozenset[TagId] = frozenset()
    document_type_groups: frozenset[TagId] = frozenset()

    @classmethod
    def of(
        cls,
        document: Document,
        *,
        text: DocumentText | None = None,
        patterns: Mapping[tuple[str, str, bool], bool] | None = None,
        unverified: frozenset[str] = frozenset(),
        unverified_tags: frozenset[TagId] = frozenset(),
        taxonomy: Taxonomy | None = None,
    ) -> "Facts":
        taxonomy = taxonomy or Taxonomy()
        return cls(
            contact_id=document.contact_id,
            document_type_id=document.document_type_id,
            tag_ids=frozenset(document.tag_ids),
            channel=document.channel,
            document_date=document.document_date,
            fields=dict(document.fields),
            topic_id=document.topic_id,
            text=text,
            patterns=patterns or {},
            unverified=unverified,
            unverified_tags=unverified_tags & frozenset(document.tag_ids),
            taxonomy=taxonomy,
            contact_groups=taxonomy.groups_of(document.contact_id),
            document_type_groups=taxonomy.groups_of(document.document_type_id),
        )


def evaluate(
    group: Group,
    facts: Facts,
    definitions: Mapping[FieldId, FieldDefinition],
    *,
    distrust: bool = False,
) -> Truth:
    """Whether the conditions hold; with `distrust`, unknown where they depend on unconfirmed
    model values (Kleene logic: unknown and false is false, unknown or true is true)."""
    results = [
        evaluate(item, facts, definitions, distrust=distrust)
        if isinstance(item, Group)
        else _condition(item, facts, definitions, distrust)
        for item in group.items
    ]
    if group.mode == "all":
        result: Truth = False if False in results else (None if None in results else True)
    else:
        result = True if True in results else (None if None in results else False)
    if group.negate and result is not None:
        return not result
    return result


def distrusted_fields(group: Group, facts: Facts) -> tuple[str, ...]:
    """The unconfirmed model fields the conditions look at."""
    found: list[str] = []
    for condition in group.conditions():
        match condition.field:
            case ConditionField.CONTACT if CONTACT in facts.unverified:
                found.append(CONTACT)
            case ConditionField.DOCUMENT_TYPE if DOCUMENT_TYPE in facts.unverified:
                found.append(DOCUMENT_TYPE)
            case ConditionField.CONTACT_GROUP if CONTACT in facts.unverified:
                found.append(CONTACT)
            case ConditionField.DOCUMENT_TYPE_GROUP if DOCUMENT_TYPE in facts.unverified:
                found.append(DOCUMENT_TYPE)
            case ConditionField.TOPIC if TOPIC in facts.unverified:
                found.append(TOPIC)
            case ConditionField.TAGS if facts.unverified_tags:
                found.append(TAGS)
    return tuple(dict.fromkeys(found))


def _condition(
    condition: Condition,
    facts: Facts,
    definitions: Mapping[FieldId, FieldDefinition],
    distrust: bool,
) -> Truth:
    op, values = condition.op, condition.values
    match condition.field:
        case ConditionField.CONTACT | ConditionField.DOCUMENT_TYPE:
            field_name = condition.field.value
            current = facts.contact_id if field_name == CONTACT else facts.document_type_id
            if distrust and field_name in facts.unverified:
                return None
            return _reference(op, None if current is None else str(current), values)
        case ConditionField.TAGS:
            return _tags(op, values, facts, distrust)
        case ConditionField.CONTACT_GROUP | ConditionField.DOCUMENT_TYPE_GROUP:
            owner = CONTACT if condition.field is ConditionField.CONTACT_GROUP else DOCUMENT_TYPE
            if distrust and owner in facts.unverified:
                return None
            groups = facts.contact_groups if owner == CONTACT else facts.document_type_groups
            return _held(op, values, facts.taxonomy.tree.with_ancestors(groups))
        case ConditionField.TOPIC:
            if distrust and TOPIC in facts.unverified:
                return None
            topic = () if facts.topic_id is None else (facts.topic_id,)
            return _held(op, values, facts.taxonomy.tree.with_ancestors(topic))
        case ConditionField.CHANNEL:
            return facts.channel.value in values
        case ConditionField.TEXT:
            if facts.text is None:
                return False
            if op is Operator.MATCHES:
                key = (TEXT_TARGET, str(condition.value), condition.case_sensitive)
                return facts.patterns.get(key, False)
            return facts.text.contains(str(condition.value))
        case ConditionField.DOCUMENT_DATE:
            return _ordered(op, facts.document_date, [_date(value) for value in values])
        case ConditionField.FIELD:
            assert condition.field_id is not None
            definition = definitions.get(condition.field_id)
            if definition is None:
                return False
            return _field(condition, definition, facts)


def _reference(op: Operator, current: str | None, values: list[JsonValue]) -> bool:
    match op:
        case Operator.PRESENT:
            return current is not None
        case Operator.MISSING:
            return current is None
    return current is not None and current in {str(value) for value in values}


def _tags(op: Operator, values: list[JsonValue], facts: Facts, distrust: bool) -> Truth:
    """A tag wanted matches the tags set below it: the document has it as an ancestor."""
    unverified = facts.unverified_tags if distrust else frozenset()
    verified = facts.tag_ids - unverified
    tree = facts.taxonomy.tree
    wanted = {TagId(UUID(str(value))) for value in values}
    match op:
        case Operator.PRESENT:
            return True if verified else (None if unverified else False)
        case Operator.MISSING:
            return False if verified else (None if unverified else True)
    if wanted & tree.with_ancestors(verified):
        return True
    return None if wanted & tree.with_ancestors(unverified) else False


def _held(op: Operator, values: list[JsonValue], held: frozenset[TagId]) -> bool:
    """A condition on groups (or the topic), against the groups held with their ancestors."""
    match op:
        case Operator.PRESENT:
            return bool(held)
        case Operator.MISSING:
            return not held
    return bool({TagId(UUID(str(value))) for value in values} & held)


def _ordered[T: (date, Decimal)](op: Operator, current: T | None, values: list[T]) -> bool:
    match op:
        case Operator.PRESENT:
            return current is not None
        case Operator.MISSING:
            return current is None
    if current is None or not values:
        return False
    match op:
        case Operator.GT:
            return current > values[0]
        case Operator.LT:
            return current < values[0]
    return current in values


def _field(condition: Condition, definition: FieldDefinition, facts: Facts) -> bool:
    op = condition.op
    value = facts.fields.get(definition.id)
    if op is Operator.PRESENT:
        return value is not None
    if op is Operator.MISSING:
        return value is None
    if value is None:
        return False
    if op is Operator.MATCHES:
        key = (str(definition.id), str(condition.value), condition.case_sensitive)
        return facts.patterns.get(key, False)
    if op is Operator.CONTAINS:
        return contains(normalise(_text_of(value)), str(condition.value))
    try:
        wanted = [condition_value(definition, op, item) for item in condition.values]
    except ValidationError:
        return False
    match value:
        case Period():
            return _period(op, value, wanted)
        case Money():
            same = [
                item.amount
                for item in wanted
                if isinstance(item, Money) and item.currency == value.currency
            ]
            return _ordered(op, value.amount, same)
        case bool():
            return value in wanted
        case Decimal():
            return _ordered(op, value, [item for item in wanted if isinstance(item, Decimal)])
        case date():
            return _ordered(op, value, [item for item in wanted if isinstance(item, date)])
        case str() if definition.data_type is FieldType.TEXT:
            return value.casefold() in {str(item).casefold() for item in wanted}
    return value in wanted


def _period(op: Operator, value: Period, wanted: list[FieldValue]) -> bool:
    """`is`: one of the given periods; `overlaps`: shares a day with one; `gt`: starts after
    the given date; `lt`: ends before it."""
    match op:
        case Operator.OVERLAPS:
            return any(isinstance(item, Period) and value.overlaps(item) for item in wanted)
        case Operator.GT:
            return bool(wanted) and isinstance(wanted[0], date) and value.start > wanted[0]
        case Operator.LT:
            return bool(wanted) and isinstance(wanted[0], date) and value.end < wanted[0]
    return value in wanted


def _text_of(value: FieldValue) -> str:
    return value.value if isinstance(value, Url) else str(value)


def _date(value: JsonValue) -> date:
    return date.fromisoformat(str(value))


def pattern_subjects(
    rules: Sequence[Rule], document: Document, text: str | None
) -> dict[tuple[str, str, bool], str]:
    """What each regular expression of `rules` runs against: the text or a field value
    (as text). Patterns without a subject (no text, no value) are left out."""
    subjects: dict[tuple[str, str, bool], str] = {}
    for rule in rules:
        for target, pattern, case_sensitive in rule.definition.patterns():
            subject: str | None
            if target == TEXT_TARGET:
                subject = text
            else:
                value = document.fields.get(FieldId(UUID(target)))
                subject = None if value is None else _text_of(value)
            if subject is not None:
                subjects[(target, pattern, case_sensitive)] = subject
    return subjects


def pattern_text(markdown: str, limit: int) -> str:
    """The text regular expressions run against: Markdown markup removed, whitespace collapsed,
    case kept, at most `limit` characters."""
    text = re.sub(r"[*_#|`>~]+", " ", markdown[: limit * 2])
    return re.sub(r"\s+", " ", text).strip()[:limit]


# --- matching rules ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Match:
    """A rule whose conditions hold. `trusted`: they hold without unconfirmed model values;
    otherwise `distrusted` names the model fields they rely on."""

    rule: Rule
    trusted: bool = True
    distrusted: tuple[str, ...] = ()


def matches(
    rules: Sequence[Rule],
    facts: Facts,
    definitions: Mapping[FieldId, FieldDefinition],
    *,
    before: Facts | None = None,
) -> list[Match]:
    """The rules whose conditions hold, in rule order. With `before` (a change): only those
    that did not hold before the change (they become true with it)."""
    found: list[Match] = []
    for rule in sorted(rules, key=lambda item: item.order):
        conditions = rule.definition.conditions
        if not evaluate(conditions, facts, definitions):
            continue
        if before is not None and evaluate(conditions, before, definitions):
            continue
        trusted = evaluate(conditions, facts, definitions, distrust=True) is True
        found.append(Match(rule, trusted, () if trusted else distrusted_fields(conditions, facts)))
    return found


# --- planning the change ----------------------------------------------------------------------


@dataclass(frozen=True)
class SpaceTarget:
    """What the rules need to know about a space they file into, from the owner's view.
    `shared`: other users can see documents in it (it belongs to someone else or is shared)."""

    name: str
    writable: bool
    shared: bool


@dataclass(frozen=True, kw_only=True)
class Situation:
    """Everything `plan` needs besides the matching rules.

    - `spaces`: the spaces the rules file into that exist.
    - `existing`: ids of the contacts, types, tags and fields that exist.
    - `names`: names of contacts, types, tags and spaces, for templates; `owner_name` that of
      the document's owner.
    - `render`: renders the templates of titles, text fields and review reasons (the port
      `TemplateRenderer`).
    - `model_values`: single fields that hold the value the model set, unconfirmed, as JSON.
    - `machine_title`: the title the machine gave, if the document still has it; like the
      file name, rules may replace it.
    - `locked`: single fields a person decided; `person_added_tags` and `person_removed_tags`
      likewise for tags.
    - `reviewed`: rules whose forced review a person confirmed in this processing run.
    - `accept_conflicts` (retroactive): the person accepted the rule's values over others.
    - `taxonomy`: the tag trees (tags added are document tags, the result is normalised) and
      the groups of the contacts and types the rules may leave (for templates).
    """

    mode: Mode
    document: Document
    default_space: SpaceId
    definitions: Mapping[FieldId, FieldDefinition]
    render: Render
    owner_name: str | None = None
    spaces: Mapping[SpaceId, SpaceTarget] = field(default_factory=dict)
    existing: frozenset[object] = frozenset()
    names: Mapping[object, str] = field(default_factory=dict)
    model_values: Mapping[str, JsonValue] = field(default_factory=dict)
    machine_title: str | None = None
    locked: frozenset[str] = frozenset()
    person_added_tags: frozenset[TagId] = frozenset()
    person_removed_tags: frozenset[TagId] = frozenset()
    reviewed: frozenset[RuleId] = frozenset()
    accept_conflicts: bool = False
    taxonomy: Taxonomy = field(default_factory=Taxonomy)


@dataclass(frozen=True)
class Effect:
    field: str
    old: JsonValue
    new: JsonValue


@dataclass(frozen=True)
class Note:
    """An action that was not applied, and why (`kind`: skipped, overruled, refused,
    conflict, yielded: the title pattern gave way to another rule), or a review the rule
    forces (`review`)."""

    field: str
    kind: str
    reason: str


@dataclass
class RuleReport:
    rule_id: RuleId
    version: int
    name: str
    scope: RuleScope
    applied: list[Effect] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)

    def to_json(self, *, names: bool = True) -> JsonObject:
        data: JsonObject = {
            "rule_id": str(self.rule_id),
            "version": self.version,
            "scope": self.scope.value,
            "applied": [
                {"field": item.field, "old": item.old, "new": item.new} for item in self.applied
            ],
            "notes": [
                {"field": item.field, "kind": item.kind, "reason": item.reason}
                for item in self.notes
            ],
        }
        if names:
            data["name"] = self.name
        return data


@dataclass
class RulePlan:
    """The combined change of all matching rules.

    `checks`: per single field the rules touched, as in the model nodes: OK if applied,
    uncertain (on arrival) if a person has to decide; the inbox reads them. `reviews`:
    reasons of forced reviews that hold. `labels`: how `reasons` names a field (`field:<id>`)
    of a check, by its name."""

    changes: DocumentChanges
    space: SpaceId | None
    reports: list[RuleReport]
    checks: list[FieldCheck]
    reviews: list[str]
    labels: Mapping[str, str] = field(default_factory=dict)

    @property
    def uncertain(self) -> bool:
        return bool(self.reviews) or any(not check.ok for check in self.checks)

    @property
    def reasons(self) -> list[str]:
        return [
            f"{self.labels.get(check.field, check.field)}: {check.reason}"
            for check in self.checks
            if not check.ok
        ] + self.reviews

    @property
    def effects(self) -> list[Effect]:
        return [effect for report in self.reports for effect in report.applied]

    def to_json(self) -> JsonObject:
        return {
            "rules": [report.to_json() for report in self.reports],
            "fields": [check.to_json() for check in self.checks],
            "reviews": list(self.reviews),
        }


@dataclass
class _Candidate:
    report: RuleReport
    value: JsonValue
    match: Match


def plan(matched: Sequence[Match], situation: Situation) -> RulePlan:
    """Combine the actions of the matching rules (in rule order) into one change."""
    return _Planner(matched, situation).run()


class _Planner:
    def __init__(self, matched: Sequence[Match], situation: Situation) -> None:
        self._matched = list(matched)
        self._s = situation
        self._document = situation.document
        self._reports = [
            RuleReport(
                match.rule.id,
                match.rule.current.number,
                match.rule.definition.name,
                match.rule.scope,
            )
            for match in self._matched
        ]
        self._checks: dict[str, FieldCheck] = {}
        self._reviews: list[str] = []
        self._candidates: dict[str, list[_Candidate]] = {}
        self._adds: dict[TagId, list[RuleReport]] = {}
        self._removes: dict[TagId, list[RuleReport]] = {}
        self._titles: list[tuple[RuleReport, SetTitle, Match]] = []
        self._texts: list[tuple[RuleReport, SetField, Match]] = []
        self._review_reasons: list[tuple[RuleReport, str]] = []

    def run(self) -> RulePlan:
        for match, report in zip(self._matched, self._reports, strict=True):
            for action in match.rule.definition.actions:
                self._collect(match, report, action)
        result: dict[str, JsonValue] = {}
        self._person_topic()
        for field_name in (DOCUMENT_TYPE, CONTACT, SPACE):
            self._single(field_name, result)
        self._topic(result)
        type_after = self._type_after(result)
        self._text_fields(result, type_after)
        fields: dict[FieldId, object] = {}
        for field_name in [name for name in self._candidates if name.startswith("field:")]:
            field = FieldId(UUID(field_name.removeprefix("field:")))
            definition = self._s.definitions[field]
            if not definition.applies_to(type_after):
                for candidate in self._candidates[field_name]:
                    candidate.report.notes.append(
                        Note(field_name, "skipped", f"'{definition.name}' does not apply")
                    )
                continue
            self._single(field_name, result)
            if field_name in result:
                fields[field] = field_from_json(definition, result[field_name])
        tags = self._tags()
        values = self._values(result, type_after, fields, tags)
        self._title(result, values)
        for report, reason in self._review_reasons:
            self._review(report, self._reason(reason, values))
        changes = DocumentChanges(fields=fields)
        if CONTACT in result:
            changes = replace(changes, contact_id=ContactId(UUID(str(result[CONTACT]))))
        if DOCUMENT_TYPE in result:
            changes = replace(
                changes, document_type_id=DocumentTypeId(UUID(str(result[DOCUMENT_TYPE])))
            )
        if TOPIC in result:
            topic = result[TOPIC]
            changes = replace(changes, topic_id=None if topic is None else TagId(UUID(str(topic))))
        if TITLE in result:
            changes = replace(changes, title=str(result[TITLE]))
        if tags is not None:
            changes = replace(changes, tag_ids=tags)
        space = SpaceId(UUID(str(result[SPACE]))) if SPACE in result else None
        definitions = self._s.definitions
        return RulePlan(
            changes=changes,
            space=space,
            reports=self._reports,
            checks=list(self._checks.values()),
            reviews=self._reviews,
            labels={
                name: f"'{definitions[id].name}'"
                for name in self._checks
                if (id := _field_id(name)) is not None and id in definitions
            },
        )

    # --- collecting ---------------------------------------------------------------------------

    def _collect(self, match: Match, report: RuleReport, action: object) -> None:
        s = self._s
        match action:
            case SetSpace(space_id=space):
                self._space(match, report, space)
            case SetContact(contact_id=contact):
                self._candidate(CONTACT, str(contact), contact, match, report)
            case SetDocumentType(document_type_id=document_type):
                self._candidate(DOCUMENT_TYPE, str(document_type), document_type, match, report)
            case SetTopic(topic_id=topic):
                self._candidate(TOPIC, str(topic), topic, match, report)
            case SetTitle():
                self._titles.append((report, action, match))
            case AddTags(tag_ids=tags) | RemoveTags(tag_ids=tags):
                target = self._adds if isinstance(action, AddTags) else self._removes
                tree = s.taxonomy.tree
                for tag in sorted(tags):
                    if tag not in s.existing:
                        report.notes.append(Note(TAGS, "skipped", f"tag {tag} no longer exists"))
                    elif tag in tree and tree.tags[tag].scope is not TagScope.DOCUMENT:
                        reason = f"tag {self._tag(tag)} is a group, not a document tag"
                        report.notes.append(Note(TAGS, "skipped", reason))
                    else:
                        target.setdefault(tag, []).append(report)
            case SetField(field_id=field, value=value):
                name = field_key(field)
                definition = s.definitions.get(field)
                if definition is None:
                    report.notes.append(Note(name, "skipped", "the field no longer exists"))
                    return
                if definition.data_type is FieldType.TEXT and isinstance(value, str):
                    self._texts.append((report, action, match))
                    return
                try:
                    field_from_json(definition, value)
                except ValidationError as error:
                    report.notes.append(Note(name, "skipped", str(error)))
                    return
                self._candidates.setdefault(name, []).append(_Candidate(report, value, match))
            case ForceReview(reason=reason):
                self._review_reasons.append((report, reason))

    def _candidate(
        self, field_name: str, value: str, id: object, match: Match, report: RuleReport
    ) -> None:
        if match.rule.scope is RuleScope.GLOBAL:
            report.notes.append(Note(field_name, "refused", "global rules cannot set it"))
        elif id not in self._s.existing:
            report.notes.append(
                Note(field_name, "skipped", f"{field_name} {value} no longer exists")
            )
        else:
            self._candidates.setdefault(field_name, []).append(_Candidate(report, value, match))

    def _space(self, match: Match, report: RuleReport, space: SpaceId) -> None:
        s = self._s
        target = s.spaces.get(space)
        if match.rule.scope is RuleScope.GLOBAL:
            report.notes.append(Note(SPACE, "refused", "global rules never change visibility"))
        elif match.rule.owner_id != self._document.owner_id:
            report.notes.append(Note(SPACE, "refused", "the rule's owner does not own it"))
        elif target is None:
            report.notes.append(Note(SPACE, "skipped", f"space {space} no longer exists"))
        elif not target.writable:
            reason = f"no write access to space '{target.name}' (any more)"
            report.notes.append(Note(SPACE, "refused", reason))
            if s.mode is Mode.INGEST and SPACE not in s.locked:
                self._uncertain(SPACE, f"rule '{report.name}': {reason}", suggestion=None)
        elif target.shared and not match.trusted and s.mode is Mode.RETROACTIVE:
            # The person sees the move in the preview and may accept it, as any conflict.
            fields = ", ".join(match.distrusted)
            if s.accept_conflicts:
                self._candidates.setdefault(SPACE, []).append(_Candidate(report, str(space), match))
            else:
                reason = (
                    f"files into the shared space '{target.name}' with an unconfirmed "
                    f"{fields}; not accepted"
                )
                report.notes.append(Note(SPACE, "conflict", reason))
        elif target.shared and not match.trusted:
            fields = ", ".join(match.distrusted)
            reason = f"files into the shared space '{target.name}' only with a confirmed {fields}"
            report.notes.append(Note(SPACE, "refused", reason))
            if s.mode is Mode.INGEST:
                for name in match.distrusted:
                    self._uncertain(
                        name,
                        f"rule '{report.name}' {reason}",
                        suggestion=self._current(name),
                    )
        else:
            self._candidates.setdefault(SPACE, []).append(_Candidate(report, str(space), match))

    def _review(self, report: RuleReport, reason: str) -> None:
        s = self._s
        if s.mode is not Mode.INGEST:
            report.notes.append(Note(REVIEW, "skipped", "reviews are forced on arrival only"))
        elif report.rule_id in s.reviewed:
            report.notes.append(Note(REVIEW, "overruled", "a person confirmed the review"))
        else:
            report.notes.append(Note(REVIEW, "review", reason))
            self._reviews.append(f"rule '{report.name}': {reason}")
            self._uncertain(REVIEW, f"rule '{report.name}': {reason}", suggestion=None)

    # --- resolving ----------------------------------------------------------------------------

    def _single(self, field_name: str, result: dict[str, JsonValue]) -> None:
        candidates = self._candidates.get(field_name, [])
        if not candidates:
            return
        s = self._s
        current = self._current(field_name)
        values = list(dict.fromkeys(_key(candidate.value) for candidate in candidates))
        if len(values) > 1:
            listed = ", ".join(
                f"'{candidate.report.name}': {self._shown(field_name, candidate.value)}"
                for candidate in candidates
            )
            disagree = f"rules set different values ({listed})"
            for candidate in candidates:
                candidate.report.notes.append(Note(field_name, "conflict", disagree))
            if s.mode is Mode.INGEST and field_name not in s.locked:
                self._uncertain(field_name, disagree, suggestion=candidates[0].value)
            return
        value = candidates[0].value
        if _key(value) == _key(current):
            return
        reason: str | None = None
        kind = "conflict"
        if s.mode is Mode.RETROACTIVE:
            if not self._empty(field_name, current) and not s.accept_conflicts:
                reason = (
                    f"the document has another value ({self._shown(field_name, current)}); "
                    "not accepted"
                )
        elif field_name in s.locked:
            kind, reason = "overruled", "a person decided this field"
        elif field_name in s.model_values and _key(s.model_values[field_name]) == _key(current):
            reason = f"the model set another value ({self._shown(field_name, current)})"
            if s.mode is Mode.INGEST:
                self._uncertain(field_name, f"rules: {reason}", suggestion=value)
        if reason is not None:
            for candidate in candidates:
                candidate.report.notes.append(Note(field_name, kind, reason))
            return
        result[field_name] = value
        for candidate in candidates:
            candidate.report.applied.append(Effect(field_name, current, value))
        if s.mode is Mode.INGEST:
            self._checks.setdefault(
                field_name,
                FieldCheck(
                    field=field_name, outcome=Outcome.OK, confidence=1, value=value, proposed=value
                ),
            )

    def _person_topic(self) -> None:
        """A contact a rule sets that does not enable the topic a person chose is overruled:
        the person's topic stays, and so does the contact that enables it."""
        topic = self._document.topic_id
        if TOPIC not in self._s.locked or topic is None or CONTACT not in self._candidates:
            return
        kept = []
        for candidate in self._candidates[CONTACT]:
            if topic in self._s.taxonomy.topics_of(UUID(str(candidate.value))):
                kept.append(candidate)
            else:
                reason = f"a person chose the topic {self._tag(topic)}, which it does not enable"
                candidate.report.notes.append(Note(CONTACT, "overruled", reason))
        if kept:
            self._candidates[CONTACT] = kept
        else:
            del self._candidates[CONTACT]

    def _topic(self, result: dict[str, JsonValue]) -> None:
        """The topic the rules set, if the contact they leave enables it; a contact set that
        does not enable the document's topic clears it (an effect of the rules that set the
        contact)."""
        contact = result.get(CONTACT, self._current(CONTACT))
        contact_id = None if contact is None else UUID(str(contact))
        enabled = self._s.taxonomy.topics_of(contact_id)
        candidates = self._candidates.get(TOPIC, [])
        for candidate in candidates:
            if TagId(UUID(str(candidate.value))) not in enabled:
                named = self._shown(TOPIC, candidate.value)
                reason = (
                    "the document has no contact"
                    if contact_id is None
                    else f"its contact does not enable the topic {named}"
                )
                candidate.report.notes.append(Note(TOPIC, "skipped", reason))
        kept = [item for item in candidates if TagId(UUID(str(item.value))) in enabled]
        if kept:
            self._candidates[TOPIC] = kept
        else:
            self._candidates.pop(TOPIC, None)
        self._single(TOPIC, result)
        topic = self._document.topic_id
        if TOPIC in result or CONTACT not in result or topic is None or topic in enabled:
            return
        result[TOPIC] = None
        for candidate in self._candidates.get(CONTACT, []):
            candidate.report.applied.append(Effect(TOPIC, str(topic), None))

    def _tags(self) -> frozenset[TagId] | None:
        s = self._s
        current = frozenset(self._document.tag_ids)
        both = set(self._adds) & set(self._removes)
        for tag in sorted(both):
            reason = f"tag {self._tag(tag)} is added and removed by rules"
            for report in self._adds[tag] + self._removes[tag]:
                report.notes.append(Note(TAGS, "conflict", reason))
            if s.mode is Mode.INGEST and TAGS not in s.locked:
                self._uncertain(TAGS, reason, suggestion=None)
        added: set[TagId] = set()
        removed: set[TagId] = set()
        for tag, reports in self._adds.items():
            if tag in both or tag in current:
                continue
            if tag in s.person_removed_tags:
                for report in reports:
                    reason = f"a person removed tag {self._tag(tag)}"
                    report.notes.append(Note(TAGS, "overruled", reason))
                continue
            added.add(tag)
        for tag, reports in self._removes.items():
            if tag in both or tag not in current:
                continue
            if tag in s.person_added_tags:
                for report in reports:
                    reason = f"a person set tag {self._tag(tag)}"
                    report.notes.append(Note(TAGS, "overruled", reason))
                continue
            removed.add(tag)
            for report in reports:
                report.applied.append(Effect(TAGS, str(tag), None))
        if not added and not removed:
            return None
        # A tag above one the document keeps or gets says nothing more: it is not added.
        result = s.taxonomy.tree.normalise((current | added) - removed)
        for tag in sorted(added):
            for report in self._adds[tag]:
                if tag in result:
                    report.applied.append(Effect(TAGS, None, str(tag)))
                else:
                    reason = f"tag {self._tag(tag)} is above a tag the document has"
                    report.notes.append(Note(TAGS, "skipped", reason))
        return None if result == current else result

    def _title(self, result: dict[str, JsonValue], values: Mapping[str, TemplateValue]) -> None:
        if not self._titles:
            return
        patterns: list[_Candidate] = []
        for report, action, match in self._titles:
            pattern = match.rule.managed_by is not None
            if match.rule.scope is RuleScope.GLOBAL and not pattern:
                report.notes.append(Note(TITLE, "refused", "global rules cannot set it"))
                continue
            rendered = self._render(report, TITLE, action.template, values)
            if rendered is None:
                continue
            title = " ".join(rendered.split())
            if not title:
                report.notes.append(Note(TITLE, "skipped", "the title would be empty"))
                continue
            candidate = _Candidate(report, title, match)
            if pattern:
                patterns.append(candidate)
            else:
                self._candidates.setdefault(TITLE, []).append(candidate)
        # The type's title pattern gives way to a rule of its own that sets the title.
        if TITLE in self._candidates:
            for candidate in patterns:
                candidate.report.notes.append(Note(TITLE, "yielded", "another rule sets the title"))
        elif patterns:
            self._candidates[TITLE] = patterns
        self._single(TITLE, result)

    def _text_fields(
        self, result: Mapping[str, JsonValue], type_after: DocumentTypeId | None
    ) -> None:
        """The templates of text fields, with the contact, type and space the rules set (the
        fields as they were: rules do not see each other's fields)."""
        if not self._texts:
            return
        values = self._values(result, type_after, {}, None)
        for report, action, match in self._texts:
            name = field_key(action.field_id)
            rendered = self._render(report, name, str(action.value), values)
            if rendered is None:
                continue
            if not rendered.strip():
                report.notes.append(Note(name, "skipped", "the value would be empty"))
                continue
            value = rendered.strip()
            try:
                field_from_json(self._s.definitions[action.field_id], value)
            except ValidationError as error:
                report.notes.append(Note(name, "skipped", str(error)))
                continue
            self._candidates.setdefault(name, []).append(_Candidate(report, value, match))

    def _values(
        self,
        result: Mapping[str, JsonValue],
        type_after: DocumentTypeId | None,
        fields: Mapping[FieldId, object],
        tags: frozenset[TagId] | None,
    ) -> dict[str, TemplateValue]:
        """What templates see: the document as the rules leave it."""
        document, s = self._document, self._s
        contact = result.get(CONTACT, self._current(CONTACT))
        contact_id = None if contact is None else ContactId(UUID(str(contact)))
        topic = result.get(TOPIC, self._current(TOPIC))
        topic_id = None if topic is None else TagId(UUID(str(topic)))
        space = SpaceId(UUID(str(result[SPACE]))) if SPACE in result else document.space_id
        current: dict[FieldId, object] = {**document.fields, **fields}
        tree = s.taxonomy.tree

        def group_names(id: UUID | None) -> list[str]:
            return [tree.tags[tag].name for tag in s.taxonomy.groups_of(id) if tag in tree]

        return template_values(
            document,
            contact=None if contact_id is None else s.names.get(contact_id),
            document_type=None if type_after is None else s.names.get(type_after),
            space=s.names.get(space),
            owner=s.owner_name,
            tags=[
                s.names[tag]
                for tag in (document.tag_ids if tags is None else tags)
                if tag in s.names
            ],
            contact_groups=group_names(contact_id),
            document_type_groups=group_names(type_after),
            topic=tree.tags[topic_id].name if topic_id is not None and topic_id in tree else None,
            document_date=document.document_date,
            fields=[
                (definition, _field_value(current.get(definition.id)))
                for definition in s.definitions.values()
                if definition.applies_to(type_after)
            ],
        )

    def _reason(self, template: str, values: Mapping[str, TemplateValue]) -> str:
        """A review's reason; as written where it cannot be rendered (the review holds)."""
        try:
            return " ".join(self._s.render(template, values).split()) or template
        except (MissingValueError, ValidationError):
            return template

    def _render(
        self,
        report: RuleReport,
        field_name: str,
        template: str,
        values: Mapping[str, TemplateValue],
    ) -> str | None:
        """The rendered template; None, and why in the report, if a value is missing or the
        template cannot be rendered."""
        try:
            return self._s.render(template, values)
        except (MissingValueError, ValidationError) as error:
            report.notes.append(Note(field_name, "skipped", str(error)))
            return None

    # --- helpers ------------------------------------------------------------------------------

    def _shown(self, field_name: str, value: JsonValue) -> str:
        """`value` of `field_name` as a reason names it: a contact, type, topic or space by its
        name, a title quoted, a field's value as written (`_value_text`); the id where no name
        is known."""
        s = self._s
        if value is None:
            return "none"
        if field_name in (CONTACT, DOCUMENT_TYPE, SPACE, TOPIC):
            try:
                id = UUID(str(value))
            except ValueError:
                return str(value)
            if field_name == TOPIC:
                return self._tag(TagId(id))
            name = s.names.get(id)
            return str(value) if name is None else f"'{name}'"
        if field_name == TITLE:
            return f"'{value}'"
        field = _field_id(field_name)
        definition = None if field is None else s.definitions.get(field)
        return str(value) if definition is None else _value_text(definition, value)

    def _tag(self, tag: TagId) -> str:
        """A tag (of any scope) as a reason names it: its name quoted; its id if it is gone."""
        tree = self._s.taxonomy.tree
        name = tree.tags[tag].name if tag in tree else self._s.names.get(tag)
        return str(tag) if name is None else f"'{name}'"

    def _type_after(self, result: Mapping[str, JsonValue]) -> DocumentTypeId | None:
        if DOCUMENT_TYPE in result:
            return DocumentTypeId(UUID(str(result[DOCUMENT_TYPE])))
        return self._document.document_type_id

    def _current(self, field_name: str) -> JsonValue:
        document = self._document
        match field_name:
            case "contact":
                return None if document.contact_id is None else str(document.contact_id)
            case "document_type":
                return None if document.document_type_id is None else str(document.document_type_id)
            case "topic":
                return None if document.topic_id is None else str(document.topic_id)
            case "space":
                return str(document.space_id)
            case "title":
                return document.title
            case "tags":
                return [str(tag) for tag in sorted(document.tag_ids)]
        if field_name.startswith("field:"):
            field = FieldId(UUID(field_name.removeprefix("field:")))
            value = document.fields.get(field)
            return None if value is None else field_to_json(value)
        return None

    def _empty(self, field_name: str, current: JsonValue) -> bool:
        """For applying to existing documents: the field has no value of its own (a space
        counts as empty in the owner's default space, a title as long as it is the file
        name or the machine's)."""
        if field_name == SPACE:
            return current == str(self._s.default_space)
        if field_name == TITLE:
            return current in (_default_title(self._document), self._s.machine_title)
        return current is None

    def _uncertain(self, field_name: str, reason: str, *, suggestion: JsonValue) -> None:
        existing = self._checks.get(field_name)
        if existing is not None and not existing.ok:
            reason = f"{existing.reason}; {reason}"
            suggestion = existing.suggestion if existing.suggestion is not None else suggestion
        self._checks[field_name] = FieldCheck(
            field=field_name,
            outcome=Outcome.UNCERTAIN,
            confidence=0,
            reason=reason,
            suggestion=suggestion,
        )


def _key(value: JsonValue) -> str:
    """Comparable form of a JSON value."""
    return repr(value)


def _field_id(field_name: str) -> FieldId | None:
    """The field a check of `field:<id>` is about; None for the others."""
    if not field_name.startswith("field:"):
        return None
    try:
        return FieldId(UUID(field_name.removeprefix("field:")))
    except ValueError:
        return None


def _value_text(definition: FieldDefinition, value: JsonValue) -> str:
    """A field's value (its JSON form) as written: text and choices quoted, numbers as they
    are, an amount with its currency, a date and a period in ISO 8601, yes or no."""
    try:
        parsed = field_from_json(definition, value)
    except ValidationError:
        return str(value)
    match parsed:
        case bool():
            return "yes" if parsed else "no"
        case str():
            return f"'{parsed}'"
        case Decimal():
            return decimal_text(parsed)
        case Money():
            return f"{decimal_text(parsed.amount)} {parsed.currency}"
        case Url():
            return parsed.value
        case Period():
            return parsed.notation
        case date():
            return parsed.isoformat()


def _field_value(value: object) -> FieldValue | None:
    match value:
        case str() | bool() | Decimal() | date() | Money() | Url() | Period():
            return value
    return None


def _default_title(document: Document) -> str:
    return PurePath(document.original_filename).stem or document.original_filename


def changed_fields(changes: DocumentChanges) -> frozenset[str]:
    """The fields a change sets (given, not left unset), named as in field checks."""
    names = {
        "title": TITLE,
        "contact_id": CONTACT,
        "document_type_id": DOCUMENT_TYPE,
        "topic_id": TOPIC,
        "tag_ids": TAGS,
        "document_date": "document_date",
    }
    given = {
        field_name
        for field, field_name in names.items()
        if not isinstance(getattr(changes, field), Unset)
    }
    given.update(field_key(id) for id in changes.fields)
    return frozenset(given)
