"""Domain events as outbox rows: type, id and time in columns, the other fields as JSON."""

from datetime import datetime
from typing import Any
from uuid import UUID

from papiq.core.domain.events import (
    DocumentDeleted,
    DocumentFiled,
    DocumentReceived,
    DocumentUpdated,
    DomainEvent,
    LaneChanged,
    StepCompleted,
)
from papiq.core.domain.ids import DocumentId, DrawerId, EventId, UserId
from papiq.core.domain.json_value import JsonObject
from papiq.core.domain.pipeline import Lane, Outcome, Step


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
        case StepCompleted():
            return {
                "document_id": str(event.document_id),
                "step": event.step.value,
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
            return {"document_id": str(event.document_id), "drawer_id": str(event.drawer_id)}
        case DocumentUpdated():
            return {"document_id": str(event.document_id), "fields": list(event.fields)}
    raise ValueError(f"no outbox mapping for event type {event.type!r}")


def decode(type: str, id: UUID, occurred_at: datetime, payload: dict[str, Any]) -> DomainEvent:
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
        case StepCompleted.type:
            return StepCompleted(
                **common,
                step=Step(payload["step"]),
                run=payload["run"],
                outcome=Outcome(payload["outcome"]),
            )
        case LaneChanged.type:
            return LaneChanged(**common, old=_lane(payload["old"]), new=_lane(payload["new"]))
        case DocumentFiled.type:
            return DocumentFiled(**common, drawer_id=DrawerId(UUID(payload["drawer_id"])))
        case DocumentUpdated.type:
            return DocumentUpdated(**common, fields=tuple(payload["fields"]))
    raise ValueError(f"unknown event type {type!r}")


def _lane(value: str | None) -> Lane | None:
    return None if value is None else Lane(value)
