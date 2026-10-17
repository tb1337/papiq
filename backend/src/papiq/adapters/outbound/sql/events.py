"""Domain events as outbox rows: type, id and time in columns, the other fields as JSON."""

from datetime import datetime
from typing import Any
from uuid import UUID

from papiq.core.domain.events import (
    ClassifierTrained,
    DocumentDeleted,
    DocumentFiled,
    DocumentReceived,
    DocumentUpdated,
    DomainEvent,
    LaneChanged,
    NodeCompleted,
    NodeDeferred,
    NodeStarted,
)
from papiq.core.domain.ids import DocumentId, EventId, SpaceId, UserId
from papiq.core.domain.json_value import JsonObject
from papiq.core.domain.pipeline import Lane, Node, Outcome


def encode(event: DomainEvent) -> JsonObject:
    """The payload of an event: every field except type, id and time."""
    match event:
        case DocumentReceived():
            return {"document_id": str(event.document_id)}
        case DocumentDeleted():
            return {
                "document_id": str(event.document_id),
                "readers": [str(reader) for reader in event.readers],
            }
        case NodeStarted():
            return {
                "document_id": str(event.document_id),
                "node": event.node.value,
                "run": event.run,
            }
        case NodeDeferred():
            return {
                "document_id": str(event.document_id),
                "node": event.node.value,
                "run": event.run,
                "retry_at": event.retry_at.isoformat(),
            }
        case NodeCompleted():
            return {
                "document_id": str(event.document_id),
                "node": event.node.value,
                "run": event.run,
                "outcome": event.outcome.value,
            }
        case LaneChanged():
            return {
                "document_id": str(event.document_id),
                "old": None if event.old is None else event.old.value,
                "new": None if event.new is None else event.new.value,
            }
        case DocumentFiled():
            return {"document_id": str(event.document_id), "space_id": str(event.space_id)}
        case DocumentUpdated():
            return {"document_id": str(event.document_id), "fields": list(event.fields)}
        case ClassifierTrained():
            return {"number": event.number, "examples": event.examples}
    raise ValueError(f"no outbox mapping for event type {event.type!r}")


def decode(type: str, id: UUID, occurred_at: datetime, payload: dict[str, Any]) -> DomainEvent:
    if type == ClassifierTrained.type:
        return ClassifierTrained(
            id=EventId(id),
            occurred_at=occurred_at,
            number=payload["number"],
            examples=payload["examples"],
        )
    common: dict[str, Any] = {
        "id": EventId(id),
        "occurred_at": occurred_at,
        "document_id": DocumentId(UUID(payload["document_id"])),
    }
    match type:
        case DocumentReceived.type:
            return DocumentReceived(**common)
        case DocumentDeleted.type:
            readers = tuple(UserId(UUID(reader)) for reader in payload.get("readers", ()))
            return DocumentDeleted(**common, readers=readers)
        case NodeStarted.type:
            return NodeStarted(**common, node=Node(payload["node"]), run=payload["run"])
        case NodeDeferred.type:
            return NodeDeferred(
                **common,
                node=Node(payload["node"]),
                run=payload["run"],
                retry_at=datetime.fromisoformat(payload["retry_at"]),
            )
        case NodeCompleted.type:
            return NodeCompleted(
                **common,
                node=Node(payload["node"]),
                run=payload["run"],
                outcome=Outcome(payload["outcome"]),
            )
        case LaneChanged.type:
            return LaneChanged(**common, old=_lane(payload["old"]), new=_lane(payload["new"]))
        case DocumentFiled.type:
            return DocumentFiled(**common, space_id=SpaceId(UUID(payload["space_id"])))
        case DocumentUpdated.type:
            return DocumentUpdated(**common, fields=tuple(payload["fields"]))
    raise ValueError(f"unknown event type {type!r}")


def _lane(value: str | None) -> Lane | None:
    return None if value is None else Lane(value)
