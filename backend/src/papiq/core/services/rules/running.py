"""What every rule run needs: the rules that apply to a document, its text and pattern results,
where its values came from (model, person), and the facts about drawers and master data.

Loading the text and running the regular expressions on it happens before the unit of work that
stores the result (`prepare`), so no transaction waits for a slow pattern. Patterns on attribute
values (short) run inside it, on the state that is stored.
"""

import logging
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from papiq.core.domain.attributes import AttributeDefinition
from papiq.core.domain.classification import (
    CONTACT,
    DOCUMENT_TYPE,
    TAGS,
    attribute_field,
    attribute_to_json,
)
from papiq.core.domain.documents import Document
from papiq.core.domain.errors import NotFoundError, PatternTimeoutError, ValidationError
from papiq.core.domain.evidence import DocumentText
from papiq.core.domain.ids import AttributeId, ContactId, DocumentTypeId, DrawerId, TagId
from papiq.core.domain.json_value import JsonObject, JsonValue
from papiq.core.domain.permissions import can_file_into
from papiq.core.domain.pipeline import Outcome, Step, StepRun
from papiq.core.domain.rule_engine import (
    TEXT_TARGET,
    DrawerTarget,
    Facts,
    Mode,
    RulePlan,
    Situation,
    matches,
    pattern_subjects,
    pattern_text,
    plan,
)
from papiq.core.domain.rules import Rule, SetDrawer, Trigger
from papiq.core.domain.users import User
from papiq.core.ports import ObjectStore, PatternMatcher, UnitOfWork
from papiq.core.services.inbox import (
    MODEL_STEPS,
    PERSON,
    RULES_CHANGE,
    field_checks,
    latest_entries,
)
from papiq.core.services.objects import markdown_key

log = logging.getLogger(__name__)


type PatternKey = tuple[str, str, bool]


async def active_rules(uow: UnitOfWork, owner: User | None, trigger: Trigger | None) -> list[Rule]:
    """The enabled rules for a document of `owner`: the global ones, and the owner's own while
    the owner is active. `trigger` None: any trigger."""
    owners = [owner.id] if owner is not None and owner.active else []
    rules = await uow.rules.list_for(owners=owners, include_global=True)
    return sorted(
        (
            rule
            for rule in rules
            if rule.is_active and (trigger is None or trigger in rule.definition.triggers)
        ),
        key=lambda rule: rule.order,
    )


# --- text and patterns ------------------------------------------------------------------------


@dataclass(frozen=True)
class Prepared:
    """The document text as conditions see it, and the results of the regular expressions on
    it. `problems`: patterns that timed out or could not run (they do not match)."""

    text: DocumentText | None = None
    patterns: Mapping[PatternKey, bool] = field(default_factory=dict)
    problems: tuple[str, ...] = ()


async def prepare(
    store: ObjectStore,
    matcher: PatternMatcher,
    rules: Sequence[Rule],
    document: Document,
    *,
    max_text: int,
) -> Prepared:
    """Load the text if a rule looks at it and run the rules' patterns on it."""
    if not any(rule.definition.uses_text for rule in rules):
        return Prepared()
    if document.processing.outcomes.get(Step.PARSE) not in (Outcome.OK, Outcome.UNCERTAIN):
        return Prepared()
    try:
        markdown = (await store.get(markdown_key(document.id))).decode("utf-8")
    except NotFoundError:
        return Prepared()
    text = pattern_text(markdown, max_text)
    keys = {key for rule in rules for key in rule.definition.patterns() if key[0] == TEXT_TARGET}
    results, problems = await _search(matcher, {key: text for key in keys})
    return Prepared(DocumentText(text), results, problems)


async def attribute_patterns(
    matcher: PatternMatcher, rules: Sequence[Rule], document: Document
) -> tuple[dict[PatternKey, bool], tuple[str, ...]]:
    """The rules' patterns on the document's attribute values."""
    return await _search(matcher, pattern_subjects(rules, document, None))


async def _search(
    matcher: PatternMatcher, subjects: Mapping[PatternKey, str]
) -> tuple[dict[PatternKey, bool], tuple[str, ...]]:
    results: dict[PatternKey, bool] = {}
    problems: list[str] = []
    for key, subject in subjects.items():
        _, pattern, case_sensitive = key
        try:
            results[key] = await matcher.search(pattern, subject, case_sensitive=case_sensitive)
        except (PatternTimeoutError, ValidationError) as error:
            problems.append(str(error))
            log.warning("rule pattern skipped", extra={"pattern": pattern, "error": str(error)})
    return results, tuple(problems)


# --- where values came from -------------------------------------------------------------------

_DECISIONS = ("accepted", "entered", "kept", "changed")


@dataclass(frozen=True)
class Provenance:
    """Where the document's values came from, read from the processing log.

    - `model_values`: single fields (contact, type, date, attributes) whose value the model set
      in its latest run and no person has decided since, as JSON.
    - `model_tags`: tags the model set that no person has decided on since.
    - `person`: fields a person decided (confirming in the inbox, or changing the document)
      since the model's latest run; `tags_added` and `tags_removed` by a person likewise.
    - `confirmed_now`: a person confirmed the document in the current processing run.
    """

    model_values: Mapping[str, JsonValue]
    model_tags: frozenset[TagId]
    person: frozenset[str]
    confirmed_now: bool
    tags_added: frozenset[TagId] = frozenset()
    tags_removed: frozenset[TagId] = frozenset()

    @property
    def unverified(self) -> frozenset[str]:
        return frozenset(name for name in (CONTACT, DOCUMENT_TYPE) if name in self.model_values)


def provenance(log: Sequence[StepRun], document: Document) -> Provenance:
    latest = latest_entries(log, by_model=True)
    model_run = max((latest[step].run for step in MODEL_STEPS if step in latest), default=0)
    person: set[str] = set()
    added: set[TagId] = set()
    removed: set[TagId] = set()
    for entry in log:
        if entry.run < model_run or entry.result.model_version not in (PERSON, RULES_CHANGE):
            continue
        record = {**entry.result.input, **entry.result.output}
        for key in _DECISIONS:
            person.update(_strings(record.get(key)))
        for tag in _strings(record.get("tags_added")):
            added.add(TagId(UUID(tag)))
            removed.discard(TagId(UUID(tag)))
        for tag in _strings(record.get("tags_removed")):
            removed.add(TagId(UUID(tag)))
            added.discard(TagId(UUID(tag)))
    current = _current_values(document)
    model_values: dict[str, JsonValue] = {}
    model_tags: frozenset[TagId] = frozenset()
    for name, check in field_checks(log).items():
        if not check.ok or check.value is None or name in person:
            continue
        if name == TAGS:
            if isinstance(check.value, list):
                model_tags = frozenset(TagId(UUID(str(tag))) for tag in check.value) & frozenset(
                    document.tag_ids
                )
        elif name in current and repr(current[name]) == repr(check.value):
            model_values[name] = check.value
    run = document.processing.run
    confirmed_now = any(entry.run == run and entry.result.model_version == PERSON for entry in log)
    return Provenance(
        model_values,
        model_tags,
        frozenset(person),
        confirmed_now,
        frozenset(added),
        frozenset(removed),
    )


def _strings(value: JsonValue) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


def person_record(
    *,
    changed: Iterable[str],
    tags_before: Collection[TagId],
    tags_after: Collection[TagId],
) -> dict[str, JsonValue]:
    """What a person changed, for a log entry `provenance` reads: the fields, and the tags they
    added and removed."""
    added = sorted(str(tag) for tag in set(tags_after) - set(tags_before))
    removed = sorted(str(tag) for tag in set(tags_before) - set(tags_after))
    return {
        "changed": list[JsonValue](sorted(changed)),
        "tags_added": list[JsonValue](added),
        "tags_removed": list[JsonValue](removed),
    }


def _current_values(document: Document) -> dict[str, JsonValue]:
    values: dict[str, JsonValue] = {
        CONTACT: None if document.contact_id is None else str(document.contact_id),
        DOCUMENT_TYPE: None
        if document.document_type_id is None
        else str(document.document_type_id),
        "document_date": None
        if document.document_date is None
        else document.document_date.isoformat(),
    }
    for id, value in document.attributes.items():
        values[attribute_field(id)] = attribute_to_json(value)
    return values


def facts(
    document: Document,
    prepared: Prepared,
    patterns: Mapping[PatternKey, bool],
    origin: Provenance,
) -> Facts:
    return Facts.of(
        document,
        text=prepared.text,
        patterns={**prepared.patterns, **patterns},
        unverified=origin.unverified,
        unverified_tags=origin.model_tags,
    )


# --- the situation ----------------------------------------------------------------------------


async def situation(
    uow: UnitOfWork,
    *,
    mode: Mode,
    document: Document,
    owner: User,
    rules: Iterable[Rule],
    definitions: Mapping[AttributeId, AttributeDefinition],
    origin: Provenance,
    locked: Collection[str] = (),
    person_added_tags: frozenset[TagId] = frozenset(),
    person_removed_tags: frozenset[TagId] = frozenset(),
    accept_conflicts: bool = False,
) -> Situation:
    """Everything `plan` needs to know besides the matching rules."""
    rules = list(rules)
    references = [rule.definition.references() for rule in rules]
    existing: set[object] = set(definitions)
    names: dict[object, str] = {}
    contacts = {id for refs in references for id in refs.contacts}
    types = {id for refs in references for id in refs.document_types}
    if document.contact_id is not None:
        contacts.add(document.contact_id)
    if document.document_type_id is not None:
        types.add(document.document_type_id)
    for contact_id in contacts:
        contact = await uow.contacts.find(ContactId(contact_id))
        if contact is not None:
            existing.add(contact_id)
            names[contact_id] = contact.name
    for type_id in types:
        document_type = await uow.document_types.find(DocumentTypeId(type_id))
        if document_type is not None:
            existing.add(type_id)
            names[type_id] = document_type.name
    for tag_id in {id for refs in references for id in refs.tags}:
        if await uow.tags.find(tag_id) is not None:
            existing.add(tag_id)
    drawers: dict[DrawerId, DrawerTarget] = {}
    for rule in rules:
        for action in rule.definition.actions:
            if isinstance(action, SetDrawer) and action.drawer_id not in drawers:
                drawer = await uow.drawers.find(action.drawer_id)
                if drawer is not None:
                    drawers[drawer.id] = DrawerTarget(
                        name=drawer.name,
                        writable=can_file_into(owner, drawer),
                        shared=drawer.owner_id != owner.id or bool(drawer.shares),
                    )
    default = await uow.drawers.get_default(owner.id)
    return Situation(
        mode=mode,
        document=document,
        default_drawer=default.id,
        definitions=definitions,
        drawers=drawers,
        existing=frozenset(existing),
        names=names,
        model_values=origin.model_values,
        locked=frozenset(locked),
        person_added_tags=person_added_tags,
        person_removed_tags=person_removed_tags,
        review_confirmed=origin.confirmed_now,
        accept_conflicts=accept_conflicts,
    )


def checked_rules(rules: Iterable[Rule]) -> list[JsonValue]:
    """The rules a run looked at, for the log's input."""
    return [{"id": str(rule.id), "version": rule.current.number} for rule in rules]


# --- one run ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class RuleRun:
    """The plan of one rule run, with the patterns that could not run."""

    plan: RulePlan
    problems: tuple[str, ...]

    def output(self) -> JsonObject:
        data = self.plan.to_json()
        if self.problems:
            data["problems"] = list[JsonValue](self.problems)
        return data


async def run_rules(
    uow: UnitOfWork,
    matcher: PatternMatcher,
    *,
    mode: Mode,
    document: Document,
    owner: User,
    rules: Sequence[Rule],
    prepared: Prepared,
    origin: Provenance,
    definitions: Mapping[AttributeId, AttributeDefinition],
    before: tuple[Document, Mapping[PatternKey, bool]] | None = None,
    locked: Collection[str] = (),
    accept_conflicts: bool = False,
) -> RuleRun:
    """Evaluate `rules` on the document and plan their change (nothing is applied). `before`:
    for a change, the state before it and its attribute pattern results; only rules that become
    true with the change act."""
    patterns, problems = await attribute_patterns(matcher, rules, document)
    now_facts = facts(document, prepared, patterns, origin)
    before_facts = None
    if before is not None:
        before_facts = facts(before[0], prepared, before[1], origin)
    matched = matches(rules, now_facts, definitions, before=before_facts)
    context = await situation(
        uow,
        mode=mode,
        document=document,
        owner=owner,
        rules=[match.rule for match in matched],
        definitions=definitions,
        origin=origin,
        locked=locked,
        person_added_tags=origin.tags_added,
        person_removed_tags=origin.tags_removed,
        accept_conflicts=accept_conflicts,
    )
    return RuleRun(plan(matched, context), prepared.problems + problems)


def apply_plan(
    document: Document,
    rule_plan: RulePlan,
    definitions: Mapping[AttributeId, AttributeDefinition],
    now: datetime,
) -> str | None:
    """Apply the planned change and drawer; the reason if the change no longer fits (nothing
    is applied then)."""
    try:
        document.apply_changes(rule_plan.changes, definitions, now)
    except ValidationError as error:
        return f"the rules' change does not fit: {error}"
    if rule_plan.drawer is not None:
        document.move_to(rule_plan.drawer, now)
    return None
