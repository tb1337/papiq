"""Evaluating rules against a document and turning the actions of the matching rules into one
change, with conflicts instead of silent overwrites.

Conditions are evaluated against the state before any rule acts (a rule's action never makes
another rule match in the same run). The result of a condition is three-valued: with
`distrust`, a condition on a contact, type or tag that the model set and no person confirmed is
unknown. A rule that matches only with such values does not file into a drawer that others see
(the accepted risk of M5: a text can name a wrong contact and make it green).

How the actions of all matching rules come together (`plan`):

- A single field (drawer, contact, type, title, an attribute) set by one rule, or by several to
  the same value, is set. Several different values are a conflict: nothing is set.
- A value a person decided (confirming in the inbox, or changing the document) is never
  replaced: the rule is overruled and that is logged.
- A value the model set and no person confirmed is not replaced either: a conflict.
- Tags added and removed by rules are combined; a tag added by one rule and removed by another
  is a conflict. Removing a tag the model set is no conflict (tags are unchecked proposals).
- On arrival (`ingest`) conflicts, refused actions and forced reviews make the step uncertain:
  the document waits in the inbox. On a change by a person (`change`) nothing turns yellow (a
  filed document would disappear for everyone else): what cannot be applied is only reported.
- Applied to existing documents on request (`retroactive`), one rule: a field that has another
  value already is a conflict, applied only where the person accepted it.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal
from enum import StrEnum
from pathlib import PurePath
from uuid import UUID

from papiq.core.domain.attributes import (
    AttributeDefinition,
    AttributeType,
    AttributeValue,
    Money,
    Url,
)
from papiq.core.domain.classification import (
    CONTACT,
    DOCUMENT_TYPE,
    TAGS,
    FieldCheck,
    attribute_field,
    attribute_from_json,
    attribute_to_json,
)
from papiq.core.domain.documents import Channel, Document, DocumentChanges, Unset
from papiq.core.domain.errors import ValidationError
from papiq.core.domain.evidence import DocumentText, contains, normalise
from papiq.core.domain.ids import (
    AttributeId,
    ContactId,
    DocumentTypeId,
    DrawerId,
    RuleId,
    TagId,
)
from papiq.core.domain.json_value import JsonObject, JsonValue
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
    SetAttribute,
    SetContact,
    SetDocumentType,
    SetDrawer,
    SetTitle,
)

DRAWER = "drawer"
TITLE = "title"
REVIEW = "review"
TEXT_TARGET = "text"

type Truth = bool | None
"""Three-valued: None is unknown."""


class Mode(StrEnum):
    INGEST = "ingest"
    CHANGE = "change"
    RETROACTIVE = "retroactive"


# --- facts and evaluation ---------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class Facts:
    """A document's state as conditions see it.

    `patterns`: results of the regular expressions, by (target, pattern, case setting), target
    `text` or an attribute id; computed beforehand with a time limit. A missing result (no
    text, time out) does not match. `unverified`: the fields `contact` and `document_type` if
    they hold a value the model set and no person confirmed; `unverified_tags` likewise."""

    contact_id: ContactId | None
    document_type_id: DocumentTypeId | None
    tag_ids: frozenset[TagId]
    channel: Channel
    document_date: date | None
    attributes: Mapping[AttributeId, AttributeValue]
    text: DocumentText | None = None
    patterns: Mapping[tuple[str, str, bool], bool] = field(default_factory=dict)
    unverified: frozenset[str] = frozenset()
    unverified_tags: frozenset[TagId] = frozenset()

    @classmethod
    def of(
        cls,
        document: Document,
        *,
        text: DocumentText | None = None,
        patterns: Mapping[tuple[str, str, bool], bool] | None = None,
        unverified: frozenset[str] = frozenset(),
        unverified_tags: frozenset[TagId] = frozenset(),
    ) -> "Facts":
        return cls(
            contact_id=document.contact_id,
            document_type_id=document.document_type_id,
            tag_ids=frozenset(document.tag_ids),
            channel=document.channel,
            document_date=document.document_date,
            attributes=dict(document.attributes),
            text=text,
            patterns=patterns or {},
            unverified=unverified,
            unverified_tags=unverified_tags & frozenset(document.tag_ids),
        )


def evaluate(
    group: Group,
    facts: Facts,
    definitions: Mapping[AttributeId, AttributeDefinition],
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
            case ConditionField.TAGS if facts.unverified_tags:
                found.append(TAGS)
    return tuple(dict.fromkeys(found))


def _condition(
    condition: Condition,
    facts: Facts,
    definitions: Mapping[AttributeId, AttributeDefinition],
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
        case ConditionField.ATTRIBUTE:
            assert condition.attribute_id is not None
            definition = definitions.get(condition.attribute_id)
            if definition is None:
                return False
            return _attribute(condition, definition, facts)


def _reference(op: Operator, current: str | None, values: list[JsonValue]) -> bool:
    match op:
        case Operator.PRESENT:
            return current is not None
        case Operator.MISSING:
            return current is None
    return current is not None and current in {str(value) for value in values}


def _tags(op: Operator, values: list[JsonValue], facts: Facts, distrust: bool) -> Truth:
    unverified = facts.unverified_tags if distrust else frozenset()
    verified = facts.tag_ids - unverified
    wanted = {TagId(UUID(str(value))) for value in values}
    match op:
        case Operator.PRESENT:
            return True if verified else (None if unverified else False)
        case Operator.MISSING:
            return False if verified else (None if unverified else True)
    if wanted & verified:
        return True
    return None if wanted & unverified else False


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


def _attribute(condition: Condition, definition: AttributeDefinition, facts: Facts) -> bool:
    op = condition.op
    value = facts.attributes.get(definition.id)
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
        wanted = [attribute_from_json(definition, item) for item in condition.values]
    except ValidationError:
        return False
    match value:
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
        case str() if definition.data_type is AttributeType.TEXT:
            return value.casefold() in {str(item).casefold() for item in wanted}
    return value in wanted


def _text_of(value: AttributeValue) -> str:
    return value.value if isinstance(value, Url) else str(value)


def _date(value: JsonValue) -> date:
    return date.fromisoformat(str(value))


def pattern_subjects(
    rules: Sequence[Rule], document: Document, text: str | None
) -> dict[tuple[str, str, bool], str]:
    """What each regular expression of `rules` runs against: the text or an attribute value
    (as text). Patterns without a subject (no text, no value) are left out."""
    subjects: dict[tuple[str, str, bool], str] = {}
    for rule in rules:
        for target, pattern, case_sensitive in rule.definition.patterns():
            subject: str | None
            if target == TEXT_TARGET:
                subject = text
            else:
                value = document.attributes.get(AttributeId(UUID(target)))
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
    definitions: Mapping[AttributeId, AttributeDefinition],
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
class DrawerTarget:
    """What the rules need to know about a drawer they file into, from the owner's view.
    `shared`: other users can see documents in it (it belongs to someone else or is shared)."""

    name: str
    writable: bool
    shared: bool


@dataclass(frozen=True, kw_only=True)
class Situation:
    """Everything `plan` needs besides the matching rules.

    - `drawers`: the drawers the rules file into that exist.
    - `existing`: ids of the contacts, types, tags and attributes that exist.
    - `names`: names of contacts and types, for titles.
    - `model_values`: single fields that hold the value the model set, unconfirmed, as JSON.
    - `locked`: single fields a person decided; `person_added_tags` and `person_removed_tags`
      likewise for tags.
    - `review_confirmed`: a person confirmed the document in this processing run.
    - `accept_conflicts` (retroactive): the person accepted the rule's values over others.
    """

    mode: Mode
    document: Document
    default_drawer: DrawerId
    definitions: Mapping[AttributeId, AttributeDefinition]
    drawers: Mapping[DrawerId, DrawerTarget] = field(default_factory=dict)
    existing: frozenset[object] = frozenset()
    names: Mapping[object, str] = field(default_factory=dict)
    model_values: Mapping[str, JsonValue] = field(default_factory=dict)
    locked: frozenset[str] = frozenset()
    person_added_tags: frozenset[TagId] = frozenset()
    person_removed_tags: frozenset[TagId] = frozenset()
    review_confirmed: bool = False
    accept_conflicts: bool = False


@dataclass(frozen=True)
class Effect:
    field: str
    old: JsonValue
    new: JsonValue


@dataclass(frozen=True)
class Note:
    """An action that was not applied, and why (`kind`: skipped, overruled, refused,
    conflict, review)."""

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

    `checks`: per single field the rules touched, as in the model steps: OK if applied,
    uncertain (on arrival) if a person has to decide; the inbox reads them. `reviews`:
    reasons of forced reviews that hold."""

    changes: DocumentChanges
    drawer: DrawerId | None
    reports: list[RuleReport]
    checks: list[FieldCheck]
    reviews: list[str]

    @property
    def uncertain(self) -> bool:
        return bool(self.reviews) or any(not check.ok for check in self.checks)

    @property
    def reasons(self) -> list[str]:
        return [
            f"{check.field}: {check.reason}" for check in self.checks if not check.ok
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

    def run(self) -> RulePlan:
        for match, report in zip(self._matched, self._reports, strict=True):
            for action in match.rule.definition.actions:
                self._collect(match, report, action)
        result: dict[str, JsonValue] = {}
        for field_name in (DOCUMENT_TYPE, CONTACT, DRAWER):
            self._single(field_name, result)
        type_after = self._type_after(result)
        attributes: dict[AttributeId, object] = {}
        for field_name in [name for name in self._candidates if name.startswith("attribute:")]:
            attribute = AttributeId(UUID(field_name.removeprefix("attribute:")))
            definition = self._s.definitions[attribute]
            if not definition.applies_to(type_after):
                for candidate in self._candidates[field_name]:
                    candidate.report.notes.append(
                        Note(field_name, "skipped", f"'{definition.name}' does not apply")
                    )
                continue
            self._single(field_name, result)
            if field_name in result:
                attributes[attribute] = attribute_from_json(definition, result[field_name])
        tags = self._tags()
        self._title(result, type_after)
        changes = DocumentChanges(attributes=attributes)
        if CONTACT in result:
            changes = replace(changes, contact_id=ContactId(UUID(str(result[CONTACT]))))
        if DOCUMENT_TYPE in result:
            changes = replace(
                changes, document_type_id=DocumentTypeId(UUID(str(result[DOCUMENT_TYPE])))
            )
        if TITLE in result:
            changes = replace(changes, title=str(result[TITLE]))
        if tags is not None:
            changes = replace(changes, tag_ids=tags)
        drawer = DrawerId(UUID(str(result[DRAWER]))) if DRAWER in result else None
        return RulePlan(
            changes=changes,
            drawer=drawer,
            reports=self._reports,
            checks=list(self._checks.values()),
            reviews=self._reviews,
        )

    # --- collecting ---------------------------------------------------------------------------

    def _collect(self, match: Match, report: RuleReport, action: object) -> None:
        s = self._s
        match action:
            case SetDrawer(drawer_id=drawer):
                self._drawer(match, report, drawer)
            case SetContact(contact_id=contact):
                self._candidate(CONTACT, str(contact), contact, match, report)
            case SetDocumentType(document_type_id=document_type):
                self._candidate(DOCUMENT_TYPE, str(document_type), document_type, match, report)
            case SetTitle():
                self._titles.append((report, action, match))
            case AddTags(tag_ids=tags) | RemoveTags(tag_ids=tags):
                target = self._adds if isinstance(action, AddTags) else self._removes
                for tag in sorted(tags):
                    if tag not in s.existing:
                        report.notes.append(Note(TAGS, "skipped", f"tag {tag} no longer exists"))
                    else:
                        target.setdefault(tag, []).append(report)
            case SetAttribute(attribute_id=attribute, value=value):
                name = attribute_field(attribute)
                definition = s.definitions.get(attribute)
                if definition is None:
                    report.notes.append(Note(name, "skipped", "the attribute no longer exists"))
                    return
                try:
                    attribute_from_json(definition, value)
                except ValidationError as error:
                    report.notes.append(Note(name, "skipped", str(error)))
                    return
                self._candidates.setdefault(name, []).append(_Candidate(report, value, match))
            case ForceReview(reason=reason):
                self._review(report, reason)

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

    def _drawer(self, match: Match, report: RuleReport, drawer: DrawerId) -> None:
        s = self._s
        target = s.drawers.get(drawer)
        if match.rule.scope is RuleScope.GLOBAL:
            report.notes.append(Note(DRAWER, "refused", "global rules never change visibility"))
        elif match.rule.owner_id != self._document.owner_id:
            report.notes.append(Note(DRAWER, "refused", "the rule's owner does not own it"))
        elif target is None:
            report.notes.append(Note(DRAWER, "skipped", f"drawer {drawer} no longer exists"))
        elif not target.writable:
            reason = f"no write access to drawer '{target.name}' (any more)"
            report.notes.append(Note(DRAWER, "refused", reason))
            if s.mode is Mode.INGEST and DRAWER not in s.locked:
                self._uncertain(DRAWER, f"rule '{report.name}': {reason}", suggestion=None)
        elif target.shared and not match.trusted and s.mode is not Mode.RETROACTIVE:
            fields = ", ".join(match.distrusted)
            reason = f"files into the shared drawer '{target.name}' only with a confirmed {fields}"
            report.notes.append(Note(DRAWER, "refused", reason))
            if s.mode is Mode.INGEST:
                for name in match.distrusted:
                    self._uncertain(
                        name,
                        f"rule '{report.name}' {reason}",
                        suggestion=self._current(name),
                    )
        else:
            self._candidates.setdefault(DRAWER, []).append(_Candidate(report, str(drawer), match))

    def _review(self, report: RuleReport, reason: str) -> None:
        s = self._s
        if s.mode is not Mode.INGEST:
            report.notes.append(Note(REVIEW, "skipped", "reviews are forced on arrival only"))
        elif s.review_confirmed:
            report.notes.append(Note(REVIEW, "overruled", "a person confirmed the document"))
        else:
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
                f"'{candidate.report.name}': {candidate.value}" for candidate in candidates
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
                reason = f"the document has another value ({current}); not accepted"
        elif field_name in s.locked:
            kind, reason = "overruled", "a person decided this field"
        elif field_name in s.model_values and _key(s.model_values[field_name]) == _key(current):
            reason = f"the model set another value ({current})"
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

    def _tags(self) -> frozenset[TagId] | None:
        s = self._s
        current = frozenset(self._document.tag_ids)
        both = set(self._adds) & set(self._removes)
        for tag in sorted(both):
            reason = f"tag {tag} is added and removed by rules"
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
                    report.notes.append(Note(TAGS, "overruled", f"a person removed tag {tag}"))
                continue
            added.add(tag)
            for report in reports:
                report.applied.append(Effect(TAGS, None, str(tag)))
        for tag, reports in self._removes.items():
            if tag in both or tag not in current:
                continue
            if tag in s.person_added_tags:
                for report in reports:
                    report.notes.append(Note(TAGS, "overruled", f"a person set tag {tag}"))
                continue
            removed.add(tag)
            for report in reports:
                report.applied.append(Effect(TAGS, str(tag), None))
        if not added and not removed:
            return None
        return frozenset((current | added) - removed)

    def _title(self, result: dict[str, JsonValue], type_after: DocumentTypeId | None) -> None:
        if not self._titles:
            return
        document, s = self._document, self._s
        contact = result.get(CONTACT, self._current(CONTACT))
        names = {
            "contact": "" if contact is None else s.names.get(ContactId(UUID(str(contact))), ""),
            "document_type": "" if type_after is None else s.names.get(type_after, ""),
            "document_date": (
                "" if document.document_date is None else document.document_date.isoformat()
            ),
            "filename": _default_title(document),
        }
        for report, action, match in self._titles:
            if match.rule.scope is RuleScope.GLOBAL:
                report.notes.append(Note(TITLE, "refused", "global rules cannot set it"))
                continue
            rendered = re.sub(
                r"\{([^{}]*)\}", lambda found: names.get(found.group(1), ""), action.template
            )
            title = re.sub(r"\s+", " ", rendered).strip()
            if not title:
                report.notes.append(Note(TITLE, "skipped", "the title would be empty"))
                continue
            self._candidates.setdefault(TITLE, []).append(_Candidate(report, title, match))
        self._single(TITLE, result)

    # --- helpers ------------------------------------------------------------------------------

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
            case "drawer":
                return str(document.drawer_id)
            case "title":
                return document.title
            case "tags":
                return [str(tag) for tag in sorted(document.tag_ids)]
        if field_name.startswith("attribute:"):
            attribute = AttributeId(UUID(field_name.removeprefix("attribute:")))
            value = document.attributes.get(attribute)
            return None if value is None else attribute_to_json(value)
        return None

    def _empty(self, field_name: str, current: JsonValue) -> bool:
        """For applying to existing documents: the field has no value of its own (a drawer
        counts as empty in the owner's default drawer, a title as long as it is the file
        name)."""
        if field_name == DRAWER:
            return current == str(self._s.default_drawer)
        if field_name == TITLE:
            return current == _default_title(self._document)
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


def _default_title(document: Document) -> str:
    return PurePath(document.original_filename).stem or document.original_filename


def changed_fields(changes: DocumentChanges) -> frozenset[str]:
    """The fields a change sets (given, not left unset), named as in field checks."""
    names = {
        "title": TITLE,
        "contact_id": CONTACT,
        "document_type_id": DOCUMENT_TYPE,
        "tag_ids": TAGS,
        "document_date": "document_date",
    }
    given = {
        field_name
        for attribute, field_name in names.items()
        if not isinstance(getattr(changes, attribute), Unset)
    }
    given.update(attribute_field(id) for id in changes.attributes)
    return frozenset(given)
