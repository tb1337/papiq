"""Domain events. They are created by the aggregates and stored with the state change
(transactional outbox). Receivers recognise repeated deliveries by the event id."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import ClassVar

from papiq.core.domain.ids import DocumentId, EventId, SpaceId, UserId, new_id
from papiq.core.domain.pipeline import Lane, Node, Outcome
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
class NodeStarted(DocumentEvent):
    """A worker took the node up; again after a lost lease."""

    type: ClassVar[str] = "document.node_started"
    node: Node
    run: int


@dataclass(frozen=True, kw_only=True)
class NodeDeferred(DocumentEvent):
    """An attempt of the node failed with an error; the node waits and runs again at
    `retry_at`."""

    type: ClassVar[str] = "document.node_deferred"
    node: Node
    run: int
    retry_at: datetime

    def __post_init__(self) -> None:
        super().__post_init__()
        object.__setattr__(self, "retry_at", require_utc(self.retry_at, "retry_at"))


@dataclass(frozen=True, kw_only=True)
class NodeCompleted(DocumentEvent):
    type: ClassVar[str] = "document.node_completed"
    node: Node
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
    space_id: SpaceId


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


@dataclass(frozen=True, kw_only=True)
class ClassifierTrained(DomainEvent):
    """A new model of the classifier was stored. For the admins (event stream), not for
    webhooks."""

    type: ClassVar[str] = "classifier.trained"
    number: int
    examples: int


DOCUMENT_EVENT_TYPES: dict[str, type[DocumentEvent]] = {
    kind.type: kind
    for kind in (
        DocumentReceived,
        NodeStarted,
        NodeDeferred,
        NodeCompleted,
        LaneChanged,
        DocumentFiled,
        DocumentUpdated,
        DocumentDeleted,
    )
}
"""The events about a document: those webhooks subscribe to."""

EVENT_TYPES: dict[str, type[DomainEvent]] = {
    **DOCUMENT_EVENT_TYPES,
    ClassifierTrained.type: ClassifierTrained,
}
