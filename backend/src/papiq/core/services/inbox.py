"""The inbox: what is open in a yellow or red document, and how its owner's decision on the open
fields turns into a metadata change.

What the signals proposed and how each field was checked lies in the processing log (output
of the judge and the fields node). These functions read it from there; the rules (M7) use
`field_checks` for the values of the latest run.
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
    TOPIC,
    FieldCheck,
    checks_from_json,
    field_id_of,
)
from papiq.core.domain.documents import Document, DocumentChanges, Unset
from papiq.core.domain.errors import OpenFieldsError, ValidationError
from papiq.core.domain.fields import FieldDefinition, field_from_json
from papiq.core.domain.ids import ContactId, DocumentTypeId, FieldId, TagId
from papiq.core.domain.json_value import JsonObject, JsonValue
from papiq.core.domain.pipeline import (
    NODES,
    OUTSIDE_PIPELINE,
    PERSON,
    Node,
    NodeResult,
    NodeRun,
    Outcome,
)

ALWAYS_SET = frozenset({"space", "title", "review"})
"""Open fields of the rules that always have a value: confirming keeps it."""

MODEL_NODES = (Node.JUDGE, Node.FIELDS)
"""The nodes whose output holds field checks."""


@dataclass(frozen=True)
class OpenNode:
    """A node whose result is uncertain or failed: why, and the fields its owner has to decide
    (uncertain fields of the judge or the fields node; none for other nodes)."""

    node: Node
    outcome: Outcome
    reason: str | None
    fields: tuple[FieldCheck, ...] = ()


@dataclass(frozen=True)
class NodeReview:
    """The latest run of a model node: what was sent, what came back and how it was checked."""

    node: Node
    run: int
    outcome: Outcome
    reason: str | None
    model_version: str | None
    truncated: bool | None
    fields: tuple[FieldCheck, ...]


@dataclass(frozen=True)
class InboxItem:
    document: Document
    open: tuple[OpenNode, ...]


@dataclass(frozen=True)
class Review:
    document: Document
    open: tuple[OpenNode, ...]
    nodes: tuple[NodeReview, ...]


@dataclass(frozen=True)
class Decision:
    """The owner's decision on the open fields: the change to apply, and which fields were
    accepted as suggested, entered by the owner, or kept with the value the document has, by
    node."""

    changes: DocumentChanges
    accepted: Mapping[Node, tuple[str, ...]]
    entered: Mapping[Node, tuple[str, ...]]
    kept: Mapping[Node, tuple[str, ...]]


def latest_entries(log: Sequence[NodeRun], *, by_model: bool = False) -> dict[Node, NodeRun]:
    """The latest log entry of each node written by processing; with `by_model`, leaving out
    confirmations too."""
    latest: dict[Node, NodeRun] = {}
    for entry in log:
        if entry.result.model_version in OUTSIDE_PIPELINE:
            continue
        if by_model and entry.result.model_version == PERSON:
            continue
        latest[entry.node] = entry
    return latest


def field_checks(log: Sequence[NodeRun]) -> dict[str, FieldCheck]:
    """The field checks of the latest judge and fields node, by field."""
    latest = latest_entries(log, by_model=True)
    checks: dict[str, FieldCheck] = {}
    for node in MODEL_NODES:
        if node in latest:
            checks.update((check.field, check) for check in _checks(latest[node].result))
    return checks


def open_nodes(document: Document, log: Sequence[NodeRun]) -> tuple[OpenNode, ...]:
    """The uncertain and failed nodes of the document's current state, in graph order."""
    latest = latest_entries(log)
    result = []
    for node in NODES:
        outcome = document.processing.outcome(node)
        if outcome not in (Outcome.UNCERTAIN, Outcome.FAILED):
            continue
        entry = latest.get(node)
        reason = entry.result.reason if entry is not None else None
        fields: tuple[FieldCheck, ...] = ()
        if entry is not None and outcome is Outcome.UNCERTAIN:
            fields = tuple(check for check in _checks(entry.result) if not check.ok)
        result.append(OpenNode(node, outcome, reason, fields))
    return tuple(result)


def node_reviews(log: Sequence[NodeRun]) -> tuple[NodeReview, ...]:
    latest = latest_entries(log, by_model=True)
    reviews = []
    for node in MODEL_NODES:
        entry = latest.get(node)
        if entry is None:
            continue
        truncated = entry.result.input.get("truncated")
        reviews.append(
            NodeReview(
                node=node,
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
    open: Sequence[OpenNode],
    document: Document,
    changes: DocumentChanges,
    *,
    accept_suggestions: bool,
    definitions: Mapping[FieldId, FieldDefinition],
    given: Collection[str] = (),
) -> Decision:
    """Every open field needs a decision: a value (or None) in `changes` (or named in `given`,
    such as the space), a value the document has (its owner set it meanwhile; it is kept), or,
    with `accept_suggestions`, a suggestion that can be taken as it is. Suggestions never
    replace a value. OpenFieldsError lists the fields without a decision. Fields whose definition
    no longer exists need none. The rules' space, title and review always have a value:
    confirming keeps them."""
    accepted: dict[Node, list[str]] = {}
    entered: dict[Node, list[str]] = {}
    kept: dict[Node, list[str]] = {}
    suggested: dict[str, Any] = {}
    fields = dict(changes.fields)
    undecided: list[str] = []
    for item in open:
        for check in item.fields:
            field_id = _field_id(check.field)
            if field_id is not None and field_id not in definitions:
                continue
            if _left_empty_by_hand(check, field_id, changes, definitions):
                # A required field set by hand needs its value; none is no decision.
                undecided.append(_label(check.field, field_id, definitions))
            elif check.field in given or _given(changes, check.field, field_id):
                entered.setdefault(item.node, []).append(check.field)
            elif _has_value(document, check.field, field_id):
                kept.setdefault(item.node, []).append(check.field)
            elif accept_suggestions and check.suggestion is not None:
                value = _suggested(check, field_id, definitions)
                if field_id is not None:
                    fields[field_id] = value
                else:
                    suggested[check.field] = value
                accepted.setdefault(item.node, []).append(check.field)
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
        accepted={node: tuple(fields) for node, fields in accepted.items()},
        entered={node: tuple(fields) for node, fields in entered.items()},
        kept={node: tuple(fields) for node, fields in kept.items()},
    )


LEARNED_ALIAS = "learned_alias"
"""Key of the alias a confirmation taught a contact, in the output of its `apply_rules` entry."""
_LONGEST_ALIAS = 100  # characters; longer proposals are not learned


def alias_to_learn(
    open: Sequence[OpenNode], decision: Decision, document: Document
) -> tuple[ContactId, str] | None:
    """The contact the owner chose for an open contact field, and the name the model read for
    it: the owner entered the contact or accepted the suggestion. None if the field was not
    open, the model read no name, or the document has no contact."""
    for item in open:
        if item.node is not Node.JUDGE:
            continue
        decided = (*decision.entered.get(item.node, ()), *decision.accepted.get(item.node, ()))
        for check in item.fields:
            if check.field != CONTACT or CONTACT not in decided:
                continue
            proposed = check.proposed
            if (
                isinstance(proposed, str)
                and proposed.strip()
                and len(proposed) <= _LONGEST_ALIAS
                and document.contact_id is not None
            ):
                return document.contact_id, proposed.strip()
    return None


def confirmation(
    node: Node, before: Outcome | None, decision: Decision, confirmed_by: UUID
) -> NodeResult:
    """The log entry's result for a node the owner confirmed."""
    output: JsonObject = {
        "confirmed_by": str(confirmed_by),
        "outcome_before": None if before is None else before.value,
        "accepted": list(decision.accepted.get(node, ())),
        "entered": list(decision.entered.get(node, ())),
        "kept": list(decision.kept.get(node, ())),
    }
    return NodeResult(outcome=Outcome.OK, model_version=PERSON, output=output)


_CHANGE_FIELDS = {
    CONTACT: "contact_id",
    DOCUMENT_TYPE: "document_type_id",
    TOPIC: "topic_id",
    TAGS: "tag_ids",
    DOCUMENT_DATE: "document_date",
    "title": "title",
}


def _checks(result: NodeResult) -> list[FieldCheck]:
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


def _left_empty_by_hand(
    check: FieldCheck,
    field_id: FieldId | None,
    changes: DocumentChanges,
    definitions: Mapping[FieldId, FieldDefinition],
) -> bool:
    """Whether `check` is a field set by hand that `changes` leaves empty."""
    if field_id is None or field_id not in changes.fields or changes.fields[field_id] is not None:
        return False
    return not definitions[field_id].is_read


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
            case "topic":
                return TagId(UUID(str(value)))
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
