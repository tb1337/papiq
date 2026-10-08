"""The inbox: what is open in a yellow or red document, and how its owner's decision on the open
fields turns into a metadata change.

What the model proposed and how each field was checked lies in the processing log (output of
the classify and extract steps). These functions read it from there; the rules (M7) use
`field_checks` for the proposals of the latest model run.
"""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from typing import Any
from uuid import UUID

from papiq.core.domain.attributes import AttributeDefinition
from papiq.core.domain.classification import (
    CONTACT,
    DOCUMENT_DATE,
    DOCUMENT_TYPE,
    TAGS,
    FieldCheck,
    attribute_from_json,
    attribute_of,
    checks_from_json,
)
from papiq.core.domain.documents import Document, DocumentChanges, Unset
from papiq.core.domain.errors import OpenFieldsError, ValidationError
from papiq.core.domain.ids import AttributeId, ContactId, DocumentTypeId, TagId
from papiq.core.domain.json_value import JsonObject, JsonValue
from papiq.core.domain.pipeline import PIPELINE, Outcome, Step, StepResult, StepRun

PERSON = "person"
"""`model_version` of a log entry in which the owner confirmed a step's results."""
RULES = "rules"
"""`model_version` of the log entry of the pipeline step `apply_rules`."""
RULES_CHANGE = "rules:change"
"""`model_version` of the `apply_rules` log entry of a metadata change by a person (with the
rules it set off)."""
RULES_APPLY = "rules:apply"
"""`model_version` of the `apply_rules` log entry of a rule applied to an existing document on
request."""
PERSON_DRAWER = "person:drawer"
"""`model_version` of a log entry in which a person chose the drawer outside the inbox (an
upload into a given drawer, a move); rules leave the drawer then."""
OUTSIDE_PIPELINE = frozenset({RULES_CHANGE, RULES_APPLY, PERSON_DRAWER})
"""Log entries written outside processing; they say nothing about a step's state."""
ALWAYS_SET = frozenset({"drawer", "title", "review"})
"""Open fields of the rules that always have a value: confirming keeps it."""

MODEL_STEPS = (Step.CLASSIFY, Step.EXTRACT_ATTRIBUTES)


@dataclass(frozen=True)
class OpenStep:
    """A step whose result is uncertain or failed: why, and the fields its owner has to decide
    (uncertain fields of classification or attribute extraction; none for other steps)."""

    step: Step
    outcome: Outcome
    reason: str | None
    fields: tuple[FieldCheck, ...] = ()


@dataclass(frozen=True)
class StepReview:
    """The latest run of a model step: what was sent, what came back and how it was checked."""

    step: Step
    run: int
    outcome: Outcome
    reason: str | None
    model_version: str | None
    truncated: bool | None
    fields: tuple[FieldCheck, ...]


@dataclass(frozen=True)
class InboxItem:
    document: Document
    open: tuple[OpenStep, ...]


@dataclass(frozen=True)
class Review:
    document: Document
    open: tuple[OpenStep, ...]
    steps: tuple[StepReview, ...]


@dataclass(frozen=True)
class Decision:
    """The owner's decision on the open fields: the change to apply, and which fields were
    accepted as suggested, entered by the owner, or kept with the value the document has, by
    step."""

    changes: DocumentChanges
    accepted: Mapping[Step, tuple[str, ...]]
    entered: Mapping[Step, tuple[str, ...]]
    kept: Mapping[Step, tuple[str, ...]]


def latest_entries(log: Sequence[StepRun], *, by_model: bool = False) -> dict[Step, StepRun]:
    """The latest log entry of each step written by processing; with `by_model`, leaving out
    confirmations too."""
    latest: dict[Step, StepRun] = {}
    for entry in log:
        if entry.result.model_version in OUTSIDE_PIPELINE:
            continue
        if by_model and entry.result.model_version == PERSON:
            continue
        latest[entry.step] = entry
    return latest


def field_checks(log: Sequence[StepRun]) -> dict[str, FieldCheck]:
    """The field checks of the latest classification and attribute extraction, by field."""
    latest = latest_entries(log, by_model=True)
    checks: dict[str, FieldCheck] = {}
    for step in MODEL_STEPS:
        if step in latest:
            checks.update((check.field, check) for check in _checks(latest[step].result))
    return checks


def open_steps(document: Document, log: Sequence[StepRun]) -> tuple[OpenStep, ...]:
    """The uncertain and failed steps of the document's current state, in pipeline order."""
    latest = latest_entries(log)
    result = []
    for step in PIPELINE:
        outcome = document.processing.outcomes.get(step)
        if outcome not in (Outcome.UNCERTAIN, Outcome.FAILED):
            continue
        entry = latest.get(step)
        reason = entry.result.reason if entry is not None else None
        fields: tuple[FieldCheck, ...] = ()
        if entry is not None and outcome is Outcome.UNCERTAIN:
            fields = tuple(check for check in _checks(entry.result) if not check.ok)
        result.append(OpenStep(step, outcome, reason, fields))
    return tuple(result)


def step_reviews(log: Sequence[StepRun]) -> tuple[StepReview, ...]:
    latest = latest_entries(log, by_model=True)
    reviews = []
    for step in MODEL_STEPS:
        entry = latest.get(step)
        if entry is None:
            continue
        truncated = entry.result.input.get("truncated")
        reviews.append(
            StepReview(
                step=step,
                run=entry.run,
                outcome=entry.result.outcome,
                reason=entry.result.reason,
                model_version=entry.result.model_version,
                truncated=truncated if isinstance(truncated, bool) else None,
                fields=tuple(_checks(entry.result)),
            )
        )
    return tuple(reviews)


def decide(
    open: Sequence[OpenStep],
    document: Document,
    changes: DocumentChanges,
    *,
    accept_suggestions: bool,
    definitions: Mapping[AttributeId, AttributeDefinition],
    given: Collection[str] = (),
) -> Decision:
    """Every open field needs a decision: a value (or None) in `changes` (or named in `given`,
    such as the drawer), a value the document has (its owner set it meanwhile; it is kept), or,
    with `accept_suggestions`, a suggestion that can be taken as it is. Suggestions never
    replace a value. OpenFieldsError lists the fields without a decision. Fields of attributes
    that no longer exist need none. The rules' drawer, title and review always have a value:
    confirming keeps them."""
    accepted: dict[Step, list[str]] = {}
    entered: dict[Step, list[str]] = {}
    kept: dict[Step, list[str]] = {}
    suggested: dict[str, Any] = {}
    attributes = dict(changes.attributes)
    undecided: list[str] = []
    for item in open:
        for check in item.fields:
            attribute = _attribute_id(check.field)
            if attribute is not None and attribute not in definitions:
                continue
            if check.field in given or _given(changes, check.field, attribute):
                entered.setdefault(item.step, []).append(check.field)
            elif _has_value(document, check.field, attribute):
                kept.setdefault(item.step, []).append(check.field)
            elif accept_suggestions and check.suggestion is not None:
                value = _suggested(check, attribute, definitions)
                if attribute is not None:
                    attributes[attribute] = value
                else:
                    suggested[check.field] = value
                accepted.setdefault(item.step, []).append(check.field)
            else:
                undecided.append(_label(check.field, attribute, definitions))
    if undecided:
        raise OpenFieldsError(tuple(undecided))
    combined = replace(
        changes,
        attributes=attributes,
        **{_CHANGE_FIELDS[name]: value for name, value in suggested.items()},
    )
    return Decision(
        changes=combined,
        accepted={step: tuple(fields) for step, fields in accepted.items()},
        entered={step: tuple(fields) for step, fields in entered.items()},
        kept={step: tuple(fields) for step, fields in kept.items()},
    )


def confirmation(
    step: Step, before: Outcome | None, decision: Decision, confirmed_by: UUID
) -> StepResult:
    """The log entry's result for a step the owner confirmed."""
    output: JsonObject = {
        "confirmed_by": str(confirmed_by),
        "outcome_before": None if before is None else before.value,
        "accepted": list(decision.accepted.get(step, ())),
        "entered": list(decision.entered.get(step, ())),
        "kept": list(decision.kept.get(step, ())),
    }
    return StepResult(outcome=Outcome.OK, model_version=PERSON, output=output)


_CHANGE_FIELDS = {
    CONTACT: "contact_id",
    DOCUMENT_TYPE: "document_type_id",
    TAGS: "tag_ids",
    DOCUMENT_DATE: "document_date",
    "title": "title",
}


def _checks(result: StepResult) -> list[FieldCheck]:
    try:
        return checks_from_json(result.output.get("fields"))
    except ValidationError:
        return []


def _attribute_id(field: str) -> AttributeId | None:
    id = attribute_of(field)
    if id is None:
        return None
    try:
        return AttributeId(UUID(id))
    except ValueError:
        return None


def _given(changes: DocumentChanges, field: str, attribute: AttributeId | None) -> bool:
    if attribute is not None:
        return attribute in changes.attributes
    name = _CHANGE_FIELDS.get(field)
    return name is not None and not isinstance(getattr(changes, name), Unset)


def _has_value(document: Document, field: str, attribute: AttributeId | None) -> bool:
    if field in ALWAYS_SET:
        return True
    if attribute is not None:
        return attribute in document.attributes
    name = _CHANGE_FIELDS.get(field)
    return name is not None and getattr(document, name) not in (None, set())


def _suggested(
    check: FieldCheck,
    attribute: AttributeId | None,
    definitions: Mapping[AttributeId, AttributeDefinition],
) -> object:
    value: JsonValue = check.suggestion
    try:
        if attribute is not None:
            return attribute_from_json(definitions[attribute], value)
        match check.field:
            case "contact":
                return ContactId(UUID(str(value)))
            case "document_type":
                return DocumentTypeId(UUID(str(value)))
            case "tags" if isinstance(value, list):
                return frozenset(TagId(UUID(str(item))) for item in value)
            case "document_date":
                return date.fromisoformat(str(value))
    except ValueError:
        pass
    raise ValidationError(f"the suggestion for {check.field} cannot be taken: {value!r}")


def _label(
    field: str,
    attribute: AttributeId | None,
    definitions: Mapping[AttributeId, AttributeDefinition],
) -> str:
    if attribute is None:
        return field
    return f"{field} ({definitions[attribute].name})"
