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

from papiq.core.domain.classification import (
    CONTACT,
    DOCUMENT_DATE,
    DOCUMENT_TYPE,
    TAGS,
    FieldCheck,
    checks_from_json,
    field_from_json,
    field_id_of,
)
from papiq.core.domain.documents import Document, DocumentChanges, Unset
from papiq.core.domain.errors import OpenFieldsError, ValidationError
from papiq.core.domain.fields import FieldDefinition
from papiq.core.domain.ids import ContactId, DocumentTypeId, FieldId, TagId
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
CHOSEN_DRAWER = "drawer_id"
"""Key of the drawer a person chose, in the input of a `PERSON_DRAWER` entry and the output of a
`PERSON` entry."""
OUTSIDE_PIPELINE = frozenset({RULES_CHANGE, RULES_APPLY, PERSON_DRAWER})
"""Log entries written outside processing; they say nothing about a step's state."""
ALWAYS_SET = frozenset({"drawer", "title", "review"})
"""Open fields of the rules that always have a value: confirming keeps it."""

MODEL_STEPS = (Step.CLASSIFY, Step.EXTRACT_FIELDS)


@dataclass(frozen=True)
class OpenStep:
    """A step whose result is uncertain or failed: why, and the fields its owner has to decide
    (uncertain fields of classification or field extraction; none for other steps)."""

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
    """The field checks of the latest classification and field extraction, by field."""
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
    definitions: Mapping[FieldId, FieldDefinition],
    given: Collection[str] = (),
) -> Decision:
    """Every open field needs a decision: a value (or None) in `changes` (or named in `given`,
    such as the drawer), a value the document has (its owner set it meanwhile; it is kept), or,
    with `accept_suggestions`, a suggestion that can be taken as it is. Suggestions never
    replace a value. OpenFieldsError lists the fields without a decision. Fields of fields
    that no longer exist need none. The rules' drawer, title and review always have a value:
    confirming keeps them."""
    accepted: dict[Step, list[str]] = {}
    entered: dict[Step, list[str]] = {}
    kept: dict[Step, list[str]] = {}
    suggested: dict[str, Any] = {}
    fields = dict(changes.fields)
    undecided: list[str] = []
    for item in open:
        for check in item.fields:
            field_id = _field_id(check.field)
            if field_id is not None and field_id not in definitions:
                continue
            if check.field in given or _given(changes, check.field, field_id):
                entered.setdefault(item.step, []).append(check.field)
            elif _has_value(document, check.field, field_id):
                kept.setdefault(item.step, []).append(check.field)
            elif accept_suggestions and check.suggestion is not None:
                value = _suggested(check, field_id, definitions)
                if field_id is not None:
                    fields[field_id] = value
                else:
                    suggested[check.field] = value
                accepted.setdefault(item.step, []).append(check.field)
            else:
                undecided.append(_label(check.field, field_id, definitions))
    if undecided:
        raise OpenFieldsError(tuple(undecided))
    combined = replace(
        changes,
        fields=fields,
        **{_CHANGE_FIELDS[name]: value for name, value in suggested.items()},
    )
    return Decision(
        changes=combined,
        accepted={step: tuple(fields) for step, fields in accepted.items()},
        entered={step: tuple(fields) for step, fields in entered.items()},
        kept={step: tuple(fields) for step, fields in kept.items()},
    )


def drawer_chooser(log: Sequence[StepRun], drawer: UUID) -> UUID | None:
    """Who last put the document into `drawer` by hand (confirming, uploading into a given
    drawer, moving), if anyone. Entries written before the drawer was recorded name none."""
    for entry in reversed(log):
        result = entry.result
        if result.model_version == PERSON_DRAWER:
            actor, chosen = result.input.get("actor"), result.input.get(CHOSEN_DRAWER)
        elif result.model_version == PERSON:
            actor, chosen = result.output.get("confirmed_by"), result.output.get(CHOSEN_DRAWER)
        else:
            continue
        if chosen == str(drawer):
            return UUID(actor) if isinstance(actor, str) else None
    return None


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


def _field_id(field: str) -> FieldId | None:
    id = field_id_of(field)
    if id is None:
        return None
    try:
        return FieldId(UUID(id))
    except ValueError:
        return None


def _given(changes: DocumentChanges, field: str, field_id: FieldId | None) -> bool:
    if field_id is not None:
        return field_id in changes.fields
    name = _CHANGE_FIELDS.get(field)
    return name is not None and not isinstance(getattr(changes, name), Unset)


def _has_value(document: Document, field: str, field_id: FieldId | None) -> bool:
    if field in ALWAYS_SET:
        return True
    if field_id is not None:
        return field_id in document.fields
    name = _CHANGE_FIELDS.get(field)
    return name is not None and getattr(document, name) not in (None, set())


def _suggested(
    check: FieldCheck,
    field_id: FieldId | None,
    definitions: Mapping[FieldId, FieldDefinition],
) -> object:
    value: JsonValue = check.suggestion
    try:
        if field_id is not None:
            return field_from_json(definitions[field_id], value)
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
    field_id: FieldId | None,
    definitions: Mapping[FieldId, FieldDefinition],
) -> str:
    if field_id is None:
        return field
    return f"{field} ({definitions[field_id].name})"
