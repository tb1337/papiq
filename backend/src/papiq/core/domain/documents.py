"""The document aggregate: metadata, processing state machine and lane."""

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from pathlib import PurePath
from typing import Self

from papiq.core.domain.attributes import AttributeDefinition, AttributeValue
from papiq.core.domain.errors import InvalidTransitionError, NotFoundError, ValidationError
from papiq.core.domain.events import (
    DocumentDeleted,
    DocumentEvent,
    DocumentFiled,
    DocumentReceived,
    DocumentUpdated,
    LaneChanged,
    StepCompleted,
)
from papiq.core.domain.ids import (
    AttributeId,
    ContactId,
    DocumentId,
    DocumentTypeId,
    DrawerId,
    TagId,
    UserId,
    new_id,
)
from papiq.core.domain.pipeline import (
    PIPELINE,
    Lane,
    Outcome,
    Processing,
    ProcessingStatus,
    Step,
    StepResult,
)
from papiq.core.domain.validation import require_name, require_utc

_SHA256 = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class Sha256:
    """SHA-256 of a document's original, lower-case hex. Also the original's object key."""

    hex: str

    def __post_init__(self) -> None:
        if not isinstance(self.hex, str) or not _SHA256.fullmatch(self.hex):
            raise ValidationError(f"invalid SHA-256 {self.hex!r}")

    @classmethod
    def of(cls, data: bytes) -> Self:
        return cls(hashlib.sha256(data).hexdigest())

    def __str__(self) -> str:
        return self.hex


class Unset(Enum):
    UNSET = "unset"


UNSET = Unset.UNSET
"""Marks a field of DocumentChanges that is left as it is."""


@dataclass(frozen=True, kw_only=True)
class DocumentChanges:
    """A metadata change. Fields left UNSET stay unchanged.

    `attributes` maps attribute ids to new raw values (checked against the definition) or to
    None to remove the value.
    """

    title: str | Unset = UNSET
    contact_id: ContactId | Unset | None = UNSET
    document_type_id: DocumentTypeId | Unset | None = UNSET
    tag_ids: frozenset[TagId] | Unset = UNSET
    document_date: date | Unset | None = UNSET
    attributes: Mapping[AttributeId, object] = field(default_factory=dict)


@dataclass(kw_only=True)
class Document:
    """A file with metadata.

    Invariants: exactly one drawer; at most one contact and one document type; attribute values
    fit their definition, and type-bound attributes only exist while the document has a matching
    type. `lane` is None while the pipeline runs and is set when it completes or fails.

    State changes record domain events; `pull_events()` hands them over for the outbox.
    """

    id: DocumentId
    owner_id: UserId
    drawer_id: DrawerId
    sha256: Sha256
    title: str
    original_filename: str
    media_type: str
    contact_id: ContactId | None = None
    document_type_id: DocumentTypeId | None = None
    tag_ids: set[TagId] = field(default_factory=set)
    attributes: dict[AttributeId, AttributeValue] = field(default_factory=dict)
    document_date: date | None = None
    lane: Lane | None = None
    processing: Processing
    created_at: datetime
    updated_at: datetime
    version: int = 1
    _events: list[DocumentEvent] = field(
        default_factory=list, init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        self.title = require_name(self.title, "title")
        self.original_filename = require_name(self.original_filename, "original filename")
        self.media_type = require_name(self.media_type, "media type")
        self.created_at = require_utc(self.created_at, "created_at")
        self.updated_at = require_utc(self.updated_at, "updated_at")

    # --- creation -------------------------------------------------------------------------------

    @classmethod
    def receive(
        cls,
        *,
        owner_id: UserId,
        drawer_id: DrawerId,
        sha256: Sha256,
        original_filename: str,
        media_type: str,
        result: StepResult,
        now: datetime,
    ) -> Self:
        """A newly received document. The receive step (hash, duplicate check, storage) is done;
        processing continues with OCR."""
        if result.outcome is Outcome.FAILED:
            raise ValidationError("a document whose receive step failed is not created")
        filename = require_name(original_filename, "original filename")
        document = cls(
            id=DocumentId(new_id()),
            owner_id=owner_id,
            drawer_id=drawer_id,
            sha256=sha256,
            title=PurePath(filename).stem or filename,
            original_filename=filename,
            media_type=media_type,
            processing=Processing(
                status=ProcessingStatus.PROCESSING, current_step=Step.RECEIVE, run=1
            ),
            created_at=now,
            updated_at=now,
        )
        document._record(DocumentReceived(document_id=document.id, occurred_at=now))
        document.record_result(Step.RECEIVE, 1, result, now)
        return document

    # --- processing state machine ---------------------------------------------------------------

    def is_awaiting(self, step: Step, run: int) -> bool:
        """True if `step` of processing run `run` is due; otherwise a job for it is stale."""
        return (
            self.processing.status is ProcessingStatus.PROCESSING
            and self.processing.current_step is step
            and self.processing.run == run
        )

    def record_result(self, step: Step, run: int, result: StepResult, now: datetime) -> Step | None:
        """Store the result of the due step and advance. Returns the next due step, if any."""
        if not self.is_awaiting(step, run):
            raise InvalidTransitionError(
                f"document {self.id}: step {step} of run {run} is not due "
                f"({self.processing.status}, {self.processing.current_step}, "
                f"run {self.processing.run})"
            )
        processing = self.processing
        processing.outcomes[step] = result.outcome
        self._record(
            StepCompleted(
                document_id=self.id, occurred_at=now, step=step, run=run, outcome=result.outcome
            )
        )
        self._touch(now)
        if result.outcome is Outcome.FAILED:
            processing.status = ProcessingStatus.FAILED
            self._set_lane(Lane.RED, now)
            return None
        if step is Step.FILE:
            self._record(
                DocumentFiled(document_id=self.id, occurred_at=now, drawer_id=self.drawer_id)
            )
        following = step.next
        if following is None:
            processing.status = ProcessingStatus.COMPLETED
            processing.current_step = None
            self._set_lane(Lane.from_outcomes(processing.outcomes.values()), now)
            return None
        processing.current_step = following
        return following

    def retry(self, now: datetime) -> Step:
        """Repeat the failed step; processing then continues with the following steps."""
        if self.processing.status is not ProcessingStatus.FAILED:
            raise InvalidTransitionError(f"document {self.id}: only failed processing is retried")
        step = self.processing.current_step
        assert step is not None  # failed processing always names its step
        return self.reprocess_from(step, now)

    def reprocess_from(self, step: Step, now: datetime) -> Step:
        """Discard the results from `step` on and process again from there."""
        processing = self.processing
        if processing.status is ProcessingStatus.PROCESSING:
            raise InvalidTransitionError(f"document {self.id} is being processed")
        if step is Step.RECEIVE:
            raise InvalidTransitionError("the receive step cannot be repeated")
        failed_at = processing.current_step
        if failed_at is not None and step.position > failed_at.position:
            raise InvalidTransitionError(f"processing failed at {failed_at}; cannot skip it")
        for later in PIPELINE[step.position :]:
            processing.outcomes.pop(later, None)
        processing.run += 1
        processing.status = ProcessingStatus.PROCESSING
        processing.current_step = step
        self._set_lane(None, now)
        self._touch(now)
        return step

    # --- metadata -------------------------------------------------------------------------------

    def apply_changes(
        self,
        changes: DocumentChanges,
        definitions: Mapping[AttributeId, AttributeDefinition],
        now: datetime,
    ) -> tuple[str, ...]:
        """Apply a metadata change; returns the names of the changed fields.

        `definitions` must contain the definitions of all attributes in the change and of all
        attributes the document has. References to contacts, types and tags are checked by the
        caller. Values of type-bound attributes that no longer apply after a type change are
        removed.
        """
        title = require_name(_pick(changes.title, self.title), "title")
        contact_id = _pick(changes.contact_id, self.contact_id)
        document_type_id = _pick(changes.document_type_id, self.document_type_id)
        tag_ids = set(_pick(changes.tag_ids, frozenset(self.tag_ids)))
        document_date = _pick(changes.document_date, self.document_date)

        attributes = dict(self.attributes)
        for attribute_id, raw in changes.attributes.items():
            definition = _definition(definitions, attribute_id)
            if raw is None:
                attributes.pop(attribute_id, None)
            elif not definition.applies_to(document_type_id):
                raise ValidationError(
                    f"attribute '{definition.name}' does not apply to this document type"
                )
            else:
                attributes[attribute_id] = definition.validate(raw)
        attributes = {
            attribute_id: value
            for attribute_id, value in attributes.items()
            if _definition(definitions, attribute_id).applies_to(document_type_id)
        }

        # Everything is valid; apply it.
        new_values: dict[str, object] = {
            "title": title,
            "contact_id": contact_id,
            "document_type_id": document_type_id,
            "tag_ids": tag_ids,
            "document_date": document_date,
            "attributes": attributes,
        }
        changed = [name for name, value in new_values.items() if getattr(self, name) != value]
        self.title = title
        self.contact_id = contact_id
        self.document_type_id = document_type_id
        self.tag_ids = tag_ids
        self.document_date = document_date
        self.attributes = attributes
        if changed:
            self._record(
                DocumentUpdated(document_id=self.id, occurred_at=now, fields=tuple(changed))
            )
            self._touch(now)
        return tuple(changed)

    def move_to(self, drawer_id: DrawerId, now: datetime) -> None:
        """File the document into another drawer. Permissions are checked by the caller."""
        if drawer_id == self.drawer_id:
            return
        self.drawer_id = drawer_id
        self._record(DocumentFiled(document_id=self.id, occurred_at=now, drawer_id=drawer_id))
        self._touch(now)

    def delete(self, now: datetime) -> None:
        """Record the deletion; the caller removes the document from the repository."""
        self._record(DocumentDeleted(document_id=self.id, occurred_at=now))

    # --- events ---------------------------------------------------------------------------------

    def pull_events(self) -> list[DocumentEvent]:
        """Hand over the recorded events and forget them."""
        events, self._events = self._events, []
        return events

    def _record(self, event: DocumentEvent) -> None:
        self._events.append(event)

    def _set_lane(self, lane: Lane | None, now: datetime) -> None:
        if lane is self.lane:
            return
        old, self.lane = self.lane, lane
        self._record(LaneChanged(document_id=self.id, occurred_at=now, old=old, new=lane))

    def _touch(self, now: datetime) -> None:
        self.updated_at = require_utc(now, "updated_at")


def _pick[T](change: T | Unset, current: T) -> T:
    return current if isinstance(change, Unset) else change


def _definition(
    definitions: Mapping[AttributeId, AttributeDefinition], attribute_id: AttributeId
) -> AttributeDefinition:
    try:
        return definitions[attribute_id]
    except KeyError:
        raise NotFoundError("attribute", attribute_id) from None
