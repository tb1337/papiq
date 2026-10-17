"""Documents: upload, details, changes, processing."""

from datetime import date, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from papiq.adapters.inbound.rest.schemas.spaces import ShareOut
from papiq.adapters.inbound.schemas import FieldJson, RuleReportOut, field_json
from papiq.core.domain.documents import Channel, Document
from papiq.core.domain.exchanges import ModelCall, RunExchange
from papiq.core.domain.permissions import can_manage_space
from papiq.core.domain.pipeline import (
    Lane,
    Node,
    NodeRun,
    NodeState,
    NodeStatus,
    Outcome,
    ProcessingStatus,
)
from papiq.core.domain.reading import DocumentReading, VisionAdvice
from papiq.core.domain.spaces import ShareLevel, Space
from papiq.core.domain.users import User
from papiq.core.services.objects import EXCHANGE_NODES

ReprocessNode = StrEnum(  # type: ignore[misc]
    "ReprocessNode", {node.name: node.value for node in Node if node is not Node.RECEIVE}
)
"""Nodes processing can restart from; receiving cannot be repeated."""

ExchangeNode = StrEnum(  # type: ignore[misc]
    "ExchangeNode", {node.name: node.value for node in EXCHANGE_NODES}
)
"""Nodes that ask a language model (OCR: the vision language model)."""


class DocumentAccepted(BaseModel):
    id: UUID
    status_url: str = Field(description="Where to follow the processing.")


class NodeStateOut(BaseModel):
    node: Node
    status: NodeStatus = Field(
        description=(
            "`waiting`: not run yet (it runs once the nodes it needs are done); `running`: a "
            "worker is at it; `done`: has an outcome; `skipped`: left out in this run."
        )
    )
    outcome: Outcome | None = Field(description="Set once the node is done.")
    started_at: datetime | None
    finished_at: datetime | None

    @classmethod
    def of(cls, node: Node, state: NodeState) -> "NodeStateOut":
        return cls(
            node=node,
            status=state.status,
            outcome=state.outcome,
            started_at=state.started_at,
            finished_at=state.finished_at,
        )


class Processing(BaseModel):
    status: ProcessingStatus = Field(
        description=(
            "`processing`: nodes are due or running; `review`: stopped before filing until the "
            "uncertain fields are confirmed; `completed`; `failed`: a node failed (nodes still "
            "running finish, nothing new starts)."
        )
    )
    run: int = Field(description="Grows with every retry or reprocessing.")
    nodes: list[NodeStateOut] = Field(
        description=(
            "The state of every node of the graph in the current run, in graph order: every "
            "node after the nodes it needs."
        )
    )
    vision: bool = Field(
        description=(
            "A person asked that this run read the pages with the vision language model "
            "(`POST /documents/{id}/vision`): the OCR node sends every page to it."
        )
    )


class DocumentDetails(BaseModel):
    """A document's metadata and processing state, with the caller's access."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "id": "01999d5e-8a7f-7c1e-b6a3-2f4d5e6f7a8b",
                    "title": "Electricity bill March",
                    "original_filename": "scan_0042.pdf",
                    "media_type": "application/pdf",
                    "channel": "web",
                    "sha256": "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
                    "owner_id": "01999d5e-1111-7c1e-b6a3-2f4d5e6f7a8b",
                    "space_id": "01999d5e-2222-7c1e-b6a3-2f4d5e6f7a8b",
                    "access": "read_write",
                    "contact_id": "01999d5e-3333-7c1e-b6a3-2f4d5e6f7a8b",
                    "document_type_id": None,
                    "topic_id": None,
                    "tag_ids": [],
                    "document_date": "2026-03-31",
                    "fields": {
                        "01999d5e-4444-7c1e-b6a3-2f4d5e6f7a8b": {
                            "amount": "84.20",
                            "currency": "EUR",
                        }
                    },
                    "lane": "green",
                    "page_count": 2,
                    "confirmed_at": None,
                    "processing": {
                        "status": "completed",
                        "run": 1,
                        "vision": False,
                        "nodes": [
                            {
                                "node": "receive",
                                "status": "done",
                                "outcome": "ok",
                                "started_at": "2026-04-02T08:15:00Z",
                                "finished_at": "2026-04-02T08:15:00Z",
                            },
                            {
                                "node": "ocr",
                                "status": "done",
                                "outcome": "ok",
                                "started_at": "2026-04-02T08:15:01Z",
                                "finished_at": "2026-04-02T08:15:40Z",
                            },
                        ],
                    },
                    "created_at": "2026-04-02T08:15:00Z",
                    "updated_at": "2026-04-02T08:16:10Z",
                }
            ]
        }
    )

    id: UUID
    title: str
    original_filename: str
    media_type: str = Field(examples=["application/pdf"])
    channel: Channel = Field(
        description="How the document arrived: `web` (web UI), `api`, `migration`."
    )
    sha256: str = Field(
        description="SHA-256 of the original file, in lower-case hex.",
        examples=["9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"],
    )
    owner_id: UUID
    space_id: UUID
    access: ShareLevel = Field(description="What the caller may do: read, or read and write.")
    contact_id: UUID | None
    document_type_id: UUID | None
    topic_id: UUID | None = Field(
        description="A topic (tag of scope `topic`) the contact enables; at most one."
    )
    tag_ids: list[UUID]
    document_date: date | None
    fields: dict[UUID, FieldJson] = Field(description="Values by field id.")
    lane: Lane | None = Field(description="None while processing runs.")
    page_count: int | None = Field(
        description="Pages of the document, once its page images are rendered (`pages` node)."
    )
    confirmed_at: datetime | None = Field(
        description=(
            "When a person last set or confirmed the metadata (in the inbox, or by changing "
            "contact, type, topic, tags, date or fields; adding or removing tags set by hand "
            "alone does not count), or it was taken over with the source system's; null while "
            "it is the pipeline's own. Confirmed documents train the classifier."
        )
    )
    processing: Processing
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, document: Document, access: ShareLevel) -> "DocumentDetails":
        state = document.processing
        return cls(
            id=document.id,
            title=document.title,
            original_filename=document.original_filename,
            media_type=document.media_type,
            channel=document.channel,
            sha256=document.sha256.hex,
            owner_id=document.owner_id,
            space_id=document.space_id,
            access=access,
            contact_id=document.contact_id,
            document_type_id=document.document_type_id,
            topic_id=document.topic_id,
            tag_ids=sorted(document.tag_ids),
            document_date=document.document_date,
            fields={key: field_json(value) for key, value in sorted(document.fields.items())},
            lane=document.lane,
            page_count=document.page_count,
            confirmed_at=document.confirmed_at,
            processing=Processing(
                status=state.status,
                run=state.run,
                nodes=[NodeStateOut.of(node, item) for node, item in state.nodes.items()],
                vision=state.vision,
            ),
            created_at=document.created_at,
            updated_at=document.updated_at,
        )


class DocumentPage(BaseModel):
    items: list[DocumentDetails]
    next_cursor: str | None = Field(
        description="Pass as `cursor` for the next page; null on the last page."
    )


class DocumentPatch(BaseModel):
    """Fields left out stay as they are; null removes a value. A new contact that does not
    enable the document's topic removes the topic; a topic given must be one the contact
    enables (422), unless an admin gives it: it is then enabled at the contact."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "title": "Electricity bill March",
                    "contact_id": "01999d5e-3333-7c1e-b6a3-2f4d5e6f7a8b",
                    "tag_ids": ["01999d5e-2222-7c1e-b6a3-2f4d5e6f7a8b"],
                    "document_date": "2026-03-31",
                    "fields": {
                        "01999d5e-4444-7c1e-b6a3-2f4d5e6f7a8b": {
                            "amount": "84.20",
                            "currency": "EUR",
                        }
                    },
                }
            ]
        },
    )

    title: str | None = Field(default=None, min_length=1, max_length=500)
    contact_id: UUID | None = None
    document_type_id: UUID | None = None
    topic_id: UUID | None = None
    tag_ids: list[UUID] | None = Field(default=None, max_length=500)
    document_date: date | None = None
    fields: dict[UUID, Any] | None = Field(
        default=None,
        description="Values by field id, in the form of `fields` above; null removes.",
    )


class ImportedMetadataIn(BaseModel):
    """The metadata a document taken over from another system comes with (form field
    `metadata` of `POST /documents`, as JSON)."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "title": "Electricity bill March",
                    "contact_id": "01999d5e-3333-7c1e-b6a3-2f4d5e6f7a8b",
                    "tag_ids": ["01999d5e-2222-7c1e-b6a3-2f4d5e6f7a8b"],
                    "document_date": "2026-03-31",
                    "fields": {"01999d5e-4444-7c1e-b6a3-2f4d5e6f7a8b": "84.20"},
                }
            ]
        },
    )

    title: str | None = Field(default=None, min_length=1, max_length=500)
    contact_id: UUID | None = None
    document_type_id: UUID | None = None
    tag_ids: list[UUID] = Field(default_factory=list, max_length=500)
    document_date: date | None = None
    fields: dict[UUID, Any] = Field(
        default_factory=dict, description="Values by field id, as in `PATCH /documents/{id}`."
    )


class MoveRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"space_id": "01999d5e-3333-7c1e-b6a3-2f4d5e6f7a8b"}]},
    )

    space_id: UUID


class DocumentTextOut(BaseModel):
    """A piece of the text read from the archive; offsets and lengths count characters."""

    text: str
    offset: int
    total_length: int
    next_offset: int | None = Field(description="Where the next piece starts; null at the end.")


class LogEntry(BaseModel):
    """One execution of one node."""

    node: Node
    run: int
    outcome: Outcome
    reason: str | None
    confidence: float | None = Field(
        description=(
            "The judge's and the fields node's summary of their checks, not a probability: the "
            "lowest of their fields' (`FieldCheckOut.confidence`)."
        )
    )
    model_version: str | None = Field(examples=["ocrmypdf 17.13.0, tesseract 5.5.0"])
    input: dict[str, Any]
    output: dict[str, Any]
    pipeline_version: str
    started_at: datetime
    duration_ms: float

    @classmethod
    def of(cls, run: NodeRun) -> "LogEntry":
        result = run.result
        return cls(
            node=run.node,
            run=run.run,
            outcome=result.outcome,
            reason=result.reason,
            confidence=result.confidence,
            model_version=result.model_version,
            input=dict(result.input),
            output=dict(result.output),
            pipeline_version=run.pipeline_version,
            started_at=run.started_at,
            duration_ms=run.duration.total_seconds() * 1000,
        )


class ModelCallOut(BaseModel):
    """One request to the language model and its answer."""

    system: str = Field(description="The system message.")
    user: str = Field(description="The user message, with the document's text.")
    answer: str = Field(description="The model's text as it came.")
    model: str = Field(examples=["qwen3:8b"])
    usage: dict[str, Any] = Field(
        description=(
            "The endpoint's usage report as it sent it: `prompt_tokens`, `completion_tokens`, "
            "with some endpoints (Ollama through OpenWebUI) durations and speeds; empty if "
            "none."
        )
    )

    @classmethod
    def of(cls, call: ModelCall) -> "ModelCallOut":
        return cls(
            system=call.system,
            user=call.user,
            answer=call.answer,
            model=call.model,
            usage=dict(call.usage),
        )


class RunExchangeOut(BaseModel):
    """What a node sent to the language model in one processing run."""

    run: int
    calls: list[ModelCallOut] = Field(
        description="One call, or two after an answer that did not fit the schema."
    )

    @classmethod
    def of(cls, exchange: RunExchange) -> "RunExchangeOut":
        return cls(run=exchange.run, calls=[ModelCallOut.of(call) for call in exchange.calls])


class ReprocessRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"from_node": "judge"}]})

    from_node: ReprocessNode = Field(
        description="Discard the results from this node on and process again from there."
    )


class DocumentChanged(DocumentDetails):
    rules: list[RuleReportOut] | None = Field(
        default=None,
        description=(
            "For the owner: the rules the change set off (they hold now and did not before) "
            "and what they did. Nothing turns yellow; what could not be applied is listed."
        ),
    )


class DocumentOutOfReach(BaseModel):
    """After a change by an editor: the owner's rules filed the document where the editor can
    no longer read it. The change is stored; nothing else about the document is shown."""

    id: UUID
    access: None = Field(default=None, description="The caller can no longer read the document.")


class VisibilityOut(BaseModel):
    """Who sees the document. Its owner always; while it is green also the space's owner and
    the users the space is shared with."""

    owner_id: UUID = Field(description="The document's owner.")
    space_id: UUID
    space_owner_id: UUID | None = Field(description="None unless the document is green.")
    shares: list[ShareOut] | None = Field(
        description=(
            "The space's shares, if the document is green and the caller owns the space or is "
            "an admin; otherwise not shown."
        )
    )

    @classmethod
    def of(cls, document: Document, space: Space, viewer: User) -> "VisibilityOut":
        green = document.lane is Lane.GREEN
        shares = None
        if green and can_manage_space(viewer, space):
            shares = [ShareOut(user_id=u, level=level) for u, level in sorted(space.shares.items())]
        return cls(
            owner_id=document.owner_id,
            space_id=space.id,
            space_owner_id=space.owner_id if green else None,
            shares=shares,
        )


class DocumentPreviewOut(BaseModel):
    """What a change would do; nothing is stored."""

    document: DocumentDetails | None = Field(
        description="The document after the change; null if the caller could not read it then."
    )
    changed: list[str] = Field(
        description="Fields that would change: `title`, `contact`, `document_type`, `topic`, "
        "`tags`, `document_date`, `field:<id>`, `space`."
    )
    rules: list[RuleReportOut] | None = Field(
        description="For the owner: the rules the change would set off, and what they would do."
    )
    visibility: VisibilityOut | None = Field(
        description="Who would see the document; null if the caller could not read it then."
    )


class PageReadingOut(BaseModel):
    page: int = Field(description="From 1.")
    words: int = Field(description="The words recognised on the page.")
    unsure: int = Field(
        description=(
            "Of them, those the reader was unsure of: below 60 % of Tesseract's confidence, "
            "or what the vision language model could not read."
        )
    )
    poor: bool = Field(
        description="The page counts as poorly read (`PAPIQ_POOR_READING_THRESHOLD`)."
    )


class ReadingOut(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "vision": False,
                    "pages": [{"page": 1, "words": 50, "unsure": 6, "poor": True}],
                    "advice": "poor_page",
                }
            ]
        }
    )

    vision: bool = Field(description="The archive's text was read by the vision language model.")
    pages: list[PageReadingOut] = Field(
        description=(
            "Each page the reader recognised, by the archive's latest OCR; none for pages "
            "with text of their own, and for documents read before the readings were logged."
        )
    )
    advice: VisionAdvice | None = Field(
        description=(
            "Why reading the document with the vision language model is advised, if it is: "
            "`no_text`, the OCR found no text (red); `poor_page`, it waits for a person and a "
            "page was read poorly. None once the model has read it."
        )
    )

    @classmethod
    def of(cls, reading: DocumentReading) -> "ReadingOut":
        return cls(
            vision=reading.vision,
            pages=[
                PageReadingOut(
                    page=page.page,
                    words=page.words,
                    unsure=page.unsure,
                    poor=page.page in reading.poor,
                )
                for page in reading.pages
            ],
            advice=reading.advice,
        )
