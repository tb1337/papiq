"""The document aggregate: metadata, processing state machine and lane."""

import hashlib
import re
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum, StrEnum
from pathlib import PurePath
from typing import Self

from papiq.core.domain.errors import InvalidTransitionError, NotFoundError, ValidationError
from papiq.core.domain.events import (
    DocumentDeleted,
    DocumentEvent,
    DocumentFiled,
    DocumentReceived,
    DocumentUpdated,
    LaneChanged,
    NodeCompleted,
    NodeDeferred,
    NodeStarted,
)
from papiq.core.domain.fields import FieldDefinition, FieldValue
from papiq.core.domain.ids import (
    ContactId,
    DocumentId,
    DocumentTypeId,
    FieldId,
    SpaceId,
    TagId,
    UserId,
    new_id,
)
from papiq.core.domain.pipeline import (
    CURRENT_SIGNALS,
    SIGNALS,
    Lane,
    Node,
    NodeResult,
    NodeState,
    NodeStatus,
    Outcome,
    Processing,
    ProcessingStatus,
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


class Channel(StrEnum):
    """How a document came in (its intake channel). Further connectors (mail, folder) follow."""

    WEB = "web"  # uploaded with a session (web UI)
    API = "api"  # uploaded with an API token
    MIGRATION = "migration"  # taken over from Paperless-ngx


class Unset(Enum):
    UNSET = "unset"


UNSET = Unset.UNSET
"""Marks a field of DocumentChanges that is left as it is."""


@dataclass(frozen=True, kw_only=True)
class DocumentChanges:
    """A metadata change. Fields left UNSET stay unchanged.

    `fields` maps field ids to new raw values (checked against the definition) or to
    None to remove the value.
    """

    title: str | Unset = UNSET
    contact_id: ContactId | Unset | None = UNSET
    document_type_id: DocumentTypeId | Unset | None = UNSET
    tag_ids: frozenset[TagId] | Unset = UNSET
    document_date: date | Unset | None = UNSET
    fields: Mapping[FieldId, object] = field(default_factory=dict)
    topic_id: TagId | Unset | None = UNSET


CONFIRMING = frozenset(
    {"contact_id", "document_type_id", "topic_id", "tag_ids", "document_date", "fields"}
)
"""Metadata a person confirms by changing it (as `Document.apply_changes` names it); a title
or a space alone does not, nor do tags set by hand alone (`confirms`)."""
CLASSIFIER_LABELS = frozenset({"contact_id", "document_type_id", "topic_id", "tag_ids"})
"""What the classifier learns from confirmed documents: the pipeline deriving them anew ends
the confirmation."""


def confirms(
    changed: Collection[str],
    tags_before: Collection[TagId],
    tags_after: Collection[TagId],
    manual: Collection[TagId],
) -> bool:
    """Whether a person's change confirms the metadata: it changed a value in `CONFIRMING`
    (`changed` as `Document.apply_changes` names them), unless all it changed are tags set by
    hand (`manual`), added or removed. A status tag such as "to pay" says nothing about contact
    and type; a change that also adds or removes an automatic tag, or changes any other
    confirming value, confirms."""
    touched = CONFIRMING & set(changed)
    if touched != {"tag_ids"}:
        return bool(touched)
    return not set(tags_before) ^ set(tags_after) <= set(manual)


@dataclass(kw_only=True)
class Document:
    """A file with metadata.

    Invariants: exactly one space; at most one contact, one document type and one topic (a
    tag of scope `topic` its contact enables; the services see to that, `_access`); field
    values fit their definition, and type-bound fields only exist while the document has a
    matching type. `lane` is None while the pipeline runs and is set when it completes or
    fails.

    State changes record domain events; `pull_events()` hands them over for the outbox.
    """

    id: DocumentId
    owner_id: UserId
    space_id: SpaceId
    sha256: Sha256
    title: str
    original_filename: str
    media_type: str
    contact_id: ContactId | None = None
    document_type_id: DocumentTypeId | None = None
    topic_id: TagId | None = None
    tag_ids: set[TagId] = field(default_factory=set)
    fields: dict[FieldId, FieldValue] = field(default_factory=dict)
    document_date: date | None = None
    channel: Channel = Channel.API
    lane: Lane | None = None
    processing: Processing
    page_count: int | None = None  # known once the pages node has rendered the page images
    # When a person last set or confirmed its metadata (in the inbox, or by changing contact,
    # type, topic, tags, date or fields; tags set by hand alone do not count, `confirms`), or
    # it was taken over with the source system's; None while its metadata is the pipeline's
    # own. Confirmed documents train the classifier.
    confirmed_at: datetime | None = None
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
        space_id: SpaceId,
        sha256: Sha256,
        original_filename: str,
        media_type: str,
        result: NodeResult,
        now: datetime,
        channel: Channel = Channel.API,
    ) -> Self:
        """A newly received document. The receive node (hash, duplicate check, storage) is done;
        the nodes after it are due."""
        if result.outcome is Outcome.FAILED:
            raise ValidationError("a document whose receive node failed is not created")
        filename = require_name(original_filename, "original filename")
        document = cls(
            id=DocumentId(new_id()),
            owner_id=owner_id,
            space_id=space_id,
            sha256=sha256,
            title=PurePath(filename).stem or filename,
            original_filename=filename,
            media_type=media_type,
            channel=channel,
            processing=Processing.new(),
            created_at=now,
            updated_at=now,
        )
        document._record(DocumentReceived(document_id=document.id, occurred_at=now))
        document.record_result(Node.RECEIVE, 1, result, now)
        return document

    # --- processing state machine ---------------------------------------------------------------

    def is_awaiting(self, node: Node, run: int) -> bool:
        """True if `node` of processing run `run` may run or is running; otherwise a job for it
        is stale. After a failure, the nodes still running finish, but nothing new starts."""
        processing = self.processing
        if run != processing.run:
            return False
        if processing.nodes[node].status is NodeStatus.RUNNING:
            return processing.status in (ProcessingStatus.PROCESSING, ProcessingStatus.FAILED)
        return processing.status is ProcessingStatus.PROCESSING and node in processing.due()

    def start_node(self, node: Node, run: int, now: datetime) -> None:
        """A worker takes the due node up (again, after a lost lease or a deferral)."""
        self._require_awaiting(node, run)
        self.processing.nodes[node] = NodeState(status=NodeStatus.RUNNING, started_at=now)
        self._record(NodeStarted(document_id=self.id, occurred_at=now, node=node, run=run))
        self._touch(now)

    def defer_node(self, node: Node, run: int, retry_at: datetime, now: datetime) -> None:
        """An attempt of the running node failed with an error: it waits until `retry_at`,
        when a worker takes it up again (`start_node`)."""
        self._require_awaiting(node, run)
        if self.processing.nodes[node].status is not NodeStatus.RUNNING:
            raise InvalidTransitionError(f"document {self.id}: node {node} is not running")
        self.processing.nodes[node] = NodeState()
        self._record(
            NodeDeferred(
                document_id=self.id, occurred_at=now, node=node, run=run, retry_at=retry_at
            )
        )
        self._touch(now)

    def record_result(
        self, node: Node, run: int, result: NodeResult, now: datetime
    ) -> tuple[Node, ...]:
        """Store the result of a due or running node (a due node that was not started counts
        as started now). Returns the nodes that became due with it."""
        self._require_awaiting(node, run)
        processing = self.processing
        state = processing.nodes[node]
        processing.nodes[node] = NodeState(
            status=NodeStatus.DONE,
            outcome=result.outcome,
            started_at=state.started_at or now,
            finished_at=now,
        )
        self._record(
            NodeCompleted(
                document_id=self.id, occurred_at=now, node=node, run=run, outcome=result.outcome
            )
        )
        self._touch(now)
        if result.outcome is Outcome.FAILED:
            processing.status = ProcessingStatus.FAILED
            self._set_lane(Lane.RED, now)
            return ()
        if processing.status is ProcessingStatus.FAILED:
            return ()  # a node that was still running after another failed
        if node is Node.FILE and result.outcome is Outcome.OK:
            self._record(
                DocumentFiled(document_id=self.id, occurred_at=now, space_id=self.space_id)
            )
        due = processing.due()
        if not due:
            if processing.is_open():
                return ()  # the rest waits for nodes still running
            processing.status = ProcessingStatus.COMPLETED
            self._set_lane(Lane.from_outcomes(processing.outcomes.values()), now)
            return ()
        if Node.FILE in due and self._uncertain_before(Node.FILE):
            # Filed only once the owner has confirmed what is uncertain.
            processing.status = ProcessingStatus.REVIEW
            self._set_lane(Lane.YELLOW, now)
            return ()
        return tuple(following for following in due if node in following.requires)

    def retry(self, now: datetime) -> tuple[Node, ...]:
        """Repeat the failed nodes; processing then continues with the nodes after them.
        Returns the nodes that are due now."""
        if self.processing.status is not ProcessingStatus.FAILED:
            raise InvalidTransitionError(f"document {self.id}: only failed processing is retried")
        return self._reset(set(self.processing.failed()), now)

    def reprocess_from(self, node: Node, now: datetime) -> tuple[Node, ...]:
        """Discard the results of `node` and the nodes after it and process them again.
        Signals `node` needs that were skipped (a document from before they existed) run first,
        and so do those that ask no language model (`CURRENT_SIGNALS`): the judge sees the
        master data, the model and the index as they are now. Returns the nodes that are due
        now."""
        processing = self.processing
        if processing.status is ProcessingStatus.PROCESSING:
            raise InvalidTransitionError(f"document {self.id} is being processed")
        if node is Node.RECEIVE:
            raise InvalidTransitionError("the receive node cannot be repeated")
        again = self._reset_set({node})
        skipped = [failed for failed in processing.failed() if failed not in again]
        if skipped:
            raise InvalidTransitionError(
                f"processing stopped at {', '.join(skipped)}; cannot skip it"
            )
        if node is Node.FILE and self._uncertain_before(Node.FILE):
            raise InvalidTransitionError(
                f"document {self.id} has uncertain results; confirm them before filing"
            )
        signals = {
            signal
            for signal in node.requires & SIGNALS
            if signal in CURRENT_SIGNALS or processing.nodes[signal].status is NodeStatus.SKIPPED
        }
        processing.vision = False
        return self._reset({node, *signals}, now)

    def read_with_vision(self, now: datetime) -> tuple[Node, ...]:
        """A person asked that the vision language model read the pages: process again from
        OCR, which reads every page with the model in this run and its retries. As
        `reprocess_from`; returns the nodes that are due now."""
        due = self.reprocess_from(Node.OCR, now)
        self.processing.vision = True
        return due

    def confirm(self, resume_at: Node, now: datetime) -> tuple[Node, ...]:
        """The owner has decided what was uncertain or failed (the inbox): the results of the
        nodes that are not run again count as OK from now on, processing runs again from
        `resume_at` (applying rules, or extracting fields after a type correction) up to filing.
        Nodes that never ran and are not needed to resume (the pages after a failed OCR) run
        now. The metadata counts as confirmed. Returns the nodes whose results the owner
        overruled.

        Only for documents in the inbox (yellow or red, not being processed, no node still
        running after a failure). Extracting fields needs a parsed text."""
        processing = self.processing
        if self.lane not in (Lane.YELLOW, Lane.RED) or processing.status not in (
            ProcessingStatus.REVIEW,
            ProcessingStatus.FAILED,
            ProcessingStatus.COMPLETED,
        ):
            raise InvalidTransitionError(f"document {self.id} is not waiting for confirmation")
        if running := processing.running():
            # Its result would overwrite what the owner decided; it ends soon.
            raise InvalidTransitionError(
                f"document {self.id}: {', '.join(running)} is still running; confirm afterwards"
            )
        if resume_at not in (Node.FIELDS, Node.APPLY_RULES):
            raise InvalidTransitionError(
                f"processing resumes with {Node.FIELDS} or {Node.APPLY_RULES}, not {resume_at}"
            )
        if resume_at is Node.FIELDS and processing.outcome(Node.PARSE) not in (
            Outcome.OK,
            Outcome.UNCERTAIN,
        ):
            raise InvalidTransitionError(f"document {self.id} has no text to extract from")
        again = self._reset_set({resume_at})
        overruled = []
        for node, state in processing.nodes.items():
            if node in again or state.status is NodeStatus.SKIPPED or state.outcome is Outcome.OK:
                continue
            if state.status is NodeStatus.WAITING and node not in resume_at.ancestors:
                continue  # never ran and not needed to resume: it runs now
            overruled.append(node)
            processing.nodes[node] = NodeState(
                status=NodeStatus.DONE,
                outcome=Outcome.OK,
                started_at=state.started_at or now,
                finished_at=state.finished_at or now,
            )
        self._reset({resume_at}, now)
        self.confirmed_at = require_utc(now, "confirmed_at")
        return tuple(overruled)

    def set_page_count(self, count: int, now: datetime) -> None:
        """The pages node rendered `count` page images."""
        if count < 1:
            raise ValidationError(f"a document has at least one page, not {count}")
        self.page_count = count
        self._touch(now)

    def is_skipped(self, node: Node, run: int) -> bool:
        """True if `node` is skipped in run `run`: a catch-up may record its result
        (`catch_up`), also while other nodes of the run are processed."""
        processing = self.processing
        return run == processing.run and processing.nodes[node].status is NodeStatus.SKIPPED

    def catch_up(
        self, node: Node, run: int, result: NodeResult, started: datetime, now: datetime
    ) -> None:
        """Record the result of a node that is skipped in the current run, beside processing
        (the command `render-pages` catching up on page images). Status and lane stay as they
        are. A failure is recorded in the event and the log only: the node stays skipped, so
        nothing waits on it, nothing is blocked by it, and the next catch-up tries again."""
        if not self.is_skipped(node, run):
            raise InvalidTransitionError(
                f"document {self.id}: node {node} of run {run} is not skipped "
                f"({self.processing.nodes[node].status}, run {self.processing.run})"
            )
        if result.outcome is not Outcome.FAILED:
            self.processing.nodes[node] = NodeState(
                status=NodeStatus.DONE,
                outcome=result.outcome,
                started_at=started,
                finished_at=now,
            )
        self._record(
            NodeCompleted(
                document_id=self.id, occurred_at=now, node=node, run=run, outcome=result.outcome
            )
        )
        self._touch(now)

    def text_read_again(self, run: int, outcome: Outcome, now: datetime) -> None:
        """Its text was read again beside processing (the command `reparse`, after an update of
        the parser): an event for those who follow the document, so the search index takes up
        the new text. Its nodes, status and lane stay as they are."""
        if run != self.processing.run:
            raise InvalidTransitionError(
                f"document {self.id}: run {run} is not its current run {self.processing.run}"
            )
        self._record(
            NodeCompleted(
                document_id=self.id, occurred_at=now, node=Node.PARSE, run=run, outcome=outcome
            )
        )
        self._touch(now)

    def _require_awaiting(self, node: Node, run: int) -> None:
        if not self.is_awaiting(node, run):
            processing = self.processing
            raise InvalidTransitionError(
                f"document {self.id}: node {node} of run {run} is not due "
                f"({processing.status}, {processing.nodes[node].status}, run {processing.run})"
            )

    def _reset_set(self, nodes: Iterable[Node]) -> frozenset[Node]:
        """`nodes`, the nodes after them, and the nodes still running: what a new run repeats."""
        chosen = set(nodes)
        return frozenset(
            chosen
            | {later for node in chosen for later in node.descendants}
            | set(self.processing.running())
        )

    def _reset(self, nodes: Iterable[Node], now: datetime) -> tuple[Node, ...]:
        processing = self.processing
        for node in self._reset_set(nodes):
            processing.nodes[node] = NodeState()
        processing.run += 1
        processing.status = ProcessingStatus.PROCESSING
        self._set_lane(None, now)
        self._touch(now)
        return processing.due()

    def _uncertain_before(self, node: Node) -> bool:
        return any(
            self.processing.outcome(earlier) is Outcome.UNCERTAIN for earlier in node.ancestors
        )

    # --- metadata -------------------------------------------------------------------------------

    def apply_changes(
        self,
        changes: DocumentChanges,
        definitions: Mapping[FieldId, FieldDefinition],
        now: datetime,
    ) -> tuple[str, ...]:
        """Apply a metadata change; returns the names of the changed fields.

        `definitions` must contain the definitions of all fields in the change and of all
        fields the document has. References to contacts, types, topics and tags are checked by
        the caller, and so is that the contact enables the topic (`_access.checked_changes`).
        Values of type-bound fields that no longer apply after a type change are removed.
        """
        title = require_name(_pick(changes.title, self.title), "title")
        contact_id = _pick(changes.contact_id, self.contact_id)
        document_type_id = _pick(changes.document_type_id, self.document_type_id)
        topic_id = _pick(changes.topic_id, self.topic_id)
        tag_ids = set(_pick(changes.tag_ids, frozenset(self.tag_ids)))
        document_date = _pick(changes.document_date, self.document_date)

        fields = dict(self.fields)
        for field_id, raw in changes.fields.items():
            definition = _definition(definitions, field_id)
            if raw is None:
                fields.pop(field_id, None)
            elif not definition.applies_to(document_type_id):
                raise ValidationError(
                    f"field '{definition.name}' does not apply to this document type"
                )
            else:
                fields[field_id] = definition.validate(raw)
        fields = {
            field_id: value
            for field_id, value in fields.items()
            if _definition(definitions, field_id).applies_to(document_type_id)
        }

        # Everything is valid; apply it.
        new_values: dict[str, object] = {
            "title": title,
            "contact_id": contact_id,
            "document_type_id": document_type_id,
            "topic_id": topic_id,
            "tag_ids": tag_ids,
            "document_date": document_date,
            "fields": fields,
        }
        changed = [name for name, value in new_values.items() if getattr(self, name) != value]
        self.title = title
        self.contact_id = contact_id
        self.document_type_id = document_type_id
        self.topic_id = topic_id
        self.tag_ids = tag_ids
        self.document_date = document_date
        self.fields = fields
        if changed:
            self._record(
                DocumentUpdated(document_id=self.id, occurred_at=now, fields=tuple(changed))
            )
            self._touch(now)
        return tuple(changed)

    def mark_confirmed(self, now: datetime) -> None:
        """A person set or confirmed the metadata (or it was taken over)."""
        self.confirmed_at = require_utc(now, "confirmed_at")
        self._touch(now)

    def drop_confirmation(self) -> None:
        """The pipeline derived contact, type, topic or tags anew: they are no longer a
        person's."""
        self.confirmed_at = None

    def move_to(self, space_id: SpaceId, now: datetime) -> None:
        """File the document into another space. Permissions are checked by the caller."""
        if space_id == self.space_id:
            return
        self.space_id = space_id
        self._record(DocumentFiled(document_id=self.id, occurred_at=now, space_id=space_id))
        self._touch(now)

    def delete(self, now: datetime, readers: Iterable[UserId] = ()) -> None:
        """Record the deletion; the caller removes the document from the repository.
        `readers`: the users who may read the document now, for the event."""
        self._record(
            DocumentDeleted(
                document_id=self.id, occurred_at=now, readers=tuple(dict.fromkeys(readers))
            )
        )

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
    definitions: Mapping[FieldId, FieldDefinition], field_id: FieldId
) -> FieldDefinition:
    try:
        return definitions[field_id]
    except KeyError:
        raise NotFoundError("field", field_id) from None
