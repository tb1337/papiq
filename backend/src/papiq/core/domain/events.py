"""Domain events. They are created by the aggregates and stored with the state change
(transactional outbox). Receivers recognise repeated deliveries by the event id."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import ClassVar

from papiq.core.domain.ids import DocumentId, DrawerId, EventId, UserId, new_id
from papiq.core.domain.pipeline import Lane, Outcome, Step
from papiq.core.domain.validation import require_utc


@dataclass(frozen=True, kw_only=True)
class DomainEvent:
    type: ClassVar[str]
    occurred_at: datetime
    id: EventId = field(default_factory=lambda: EventId(new_id()))

    def __post_init__(self) -> None:
        object.__setattr__(self, "occurred_at", require_utc(self.occurred_at, "occurred_at"))


@dataclass(frozen=True, kw_only=True)
class DocumentEvent(DomainEvent):
    document_id: DocumentId


@dataclass(frozen=True, kw_only=True)
class DocumentReceived(DocumentEvent):
    type: ClassVar[str] = "document.received"


@dataclass(frozen=True, kw_only=True)
class StepCompleted(DocumentEvent):
    type: ClassVar[str] = "document.step_completed"
    step: Step
    run: int
    outcome: Outcome


@dataclass(frozen=True, kw_only=True)
class LaneChanged(DocumentEvent):
    """`None` means no lane: the document is being processed."""

    type: ClassVar[str] = "document.lane_changed"
    old: Lane | None
    new: Lane | None


@dataclass(frozen=True, kw_only=True)
class DocumentFiled(DocumentEvent):
    type: ClassVar[str] = "document.filed"
    drawer_id: DrawerId


@dataclass(frozen=True, kw_only=True)
class DocumentUpdated(DocumentEvent):
    type: ClassVar[str] = "document.updated"
    fields: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class DocumentDeleted(DocumentEvent):
    """`readers`: who could read the document when it was deleted (the document is gone, so
    nobody can be checked afterwards). Internal: pushed events and webhook requests do not
    carry it."""

    type: ClassVar[str] = "document.deleted"
    readers: tuple[UserId, ...] = ()


EVENT_TYPES: dict[str, type[DomainEvent]] = {
    kind.type: kind
    for kind in (
        DocumentReceived,
        StepCompleted,
        LaneChanged,
        DocumentFiled,
        DocumentUpdated,
        DocumentDeleted,
    )
}
