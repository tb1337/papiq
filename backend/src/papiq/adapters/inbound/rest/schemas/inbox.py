"""The inbox: open fields, review and confirmation."""

from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from papiq.adapters.inbound.rest.schemas.documents import DocumentDetails, DocumentPatch
from papiq.core.domain.classification import Candidate, CandidateSource, FieldCheck
from papiq.core.domain.locations import Box, Location
from papiq.core.domain.pipeline import Node, Outcome
from papiq.core.domain.spaces import ShareLevel
from papiq.core.services.inbox import InboxItem, NodeReview, OpenNode, Review

ResumeNode = StrEnum(  # type: ignore[misc]
    "ResumeNode", {node.name: node.value for node in (Node.FIELDS, Node.APPLY_RULES)}
)
"""Nodes processing can resume with after a confirmation."""


class BoxOut(BaseModel):
    """A rectangle on the page as shown (rotation applied), in fractions of its width and
    height from the top left corner."""

    x: float
    y: float
    width: float
    height: float

    @classmethod
    def of(cls, box: Box) -> "BoxOut":
        return cls(x=box.x, y=box.y, width=box.width, height=box.height)


class LocationOut(BaseModel):
    """Where a quote stands in the archive PDF."""

    page: int = Field(description="From 1.")
    boxes: list[BoxOut] = Field(description="One per line the quote covers.")

    @classmethod
    def of(cls, location: Location) -> "LocationOut":
        return cls(page=location.page, boxes=[BoxOut.of(box) for box in location.boxes])


class Signal(StrEnum):
    LLM = Node.SIGNAL_LLM.value
    CLASSIFIER = Node.SIGNAL_CLASSIFIER.value
    NEIGHBOURS = Node.SIGNAL_NEIGHBOURS.value
    FACTS = Node.SIGNAL_FACTS.value


class CandidateSourceOut(BaseModel):
    """What one signal says about a candidate."""

    signal: Signal
    names: bool = Field(
        description="Sure enough to name the value: it counts for the judge's rule."
    )
    score: float | None = Field(
        default=None,
        description=(
            "`signal_llm`: how well the name the model read matches the contact (the model "
            "gives no probability of its own); `signal_classifier`: the probability; "
            "`signal_neighbours`: the share of the similar documents that carry the value."
        ),
    )
    threshold: float | None = Field(
        default=None,
        description=(
            "The score from which the signal names a value (`signal_llm`: "
            "`PAPIQ_CONFIDENCE_THRESHOLD`; `signal_classifier`: `PAPIQ_CLASSIFIER_THRESHOLD`). "
            "The similar documents name a value by their votes instead."
        ),
    )
    similarity: float | None = Field(
        default=None,
        description=(
            "`signal_neighbours`: the highest similarity of the similar documents that carry "
            "the value, as the search index measures it."
        ),
    )
    votes: int | None = Field(
        default=None, description="`signal_neighbours`: similar documents that carry it."
    )
    found: int | None = Field(
        default=None, description="`signal_neighbours`: similar documents found."
    )
    quote: str | None = Field(default=None, description="`signal_llm`: the quoted passage.")
    identifiers: list[str] = Field(
        default_factory=list,
        description="`signal_facts`: kinds of the contact's identifiers found in the text.",
    )
    documents: list[UUID] = Field(
        default_factory=list,
        description="`signal_neighbours`: the similar documents that carry the value.",
    )
    named_in_text: bool = Field(
        default=False,
        description="`signal_facts`: the contact's name or an alias stands in the text.",
    )

    @classmethod
    def of(cls, source: CandidateSource) -> "CandidateSourceOut":
        return cls(
            signal=Signal(source.signal),
            names=source.names,
            score=None if source.score is None else round(source.score, 4),
            threshold=None if source.threshold is None else round(source.threshold, 4),
            similarity=None if source.similarity is None else round(source.similarity, 4),
            votes=source.votes,
            found=source.of,
            quote=source.quote,
            identifiers=list(source.identifiers),
            documents=[UUID(document) for document in source.documents],
            named_in_text=source.named_in_text,
        )


class CandidateOut(BaseModel):
    """A value the signals proposed for a field."""

    value: Any = Field(
        description=(
            "As the check's value (a contact, type or tag id); null for a name that does not "
            "exist yet."
        )
    )
    label: str = Field(description="The contact's, type's or tag's name, or the name read.")
    sources: list[CandidateSourceOut]
    contradicted: bool = Field(description="A signal named another value against it.")

    @classmethod
    def of(cls, candidate: Candidate) -> "CandidateOut":
        return cls(
            value=candidate.value,
            label=candidate.label,
            sources=[CandidateSourceOut.of(source) for source in candidate.sources],
            contradicted=candidate.contradicted,
        )


class FieldCheckOut(BaseModel):
    """A field the model proposed, and its check."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "field": "contact",
                    "outcome": "uncertain",
                    "confidence": 0.5,
                    "reason": "'Stadtwerke' does not appear in the text",
                    "proposed": "Stadtwerke",
                    "evidence": None,
                    "value": None,
                    "suggestion": "01999d5e-3333-7c1e-b6a3-2f4d5e6f7a8b",
                    "new_name": None,
                }
            ]
        }
    )

    field: str = Field(
        description=(
            "`contact`, `document_type`, `topic`, `tags`, `document_date`, or `field:<id>`. The "
            "topic, as the tags, is never uncertain: one not set comes as a `suggestion`."
        )
    )
    outcome: Outcome = Field(description="`ok` or `uncertain`.")
    confidence: float = Field(
        description=(
            "0 to 1, the rule's summary, not a probability: 1 for a value the judge or the "
            "checks accept, at most 0.89 for an uncertain one. The measured values are in "
            "`candidates` (their `sources`)."
        )
    )
    reason: str | None = Field(
        description="Why the field is uncertain (for the topic: why it was not set)."
    )
    proposed: Any = Field(description="What the model answered, unchanged.")
    evidence: str | None = Field(description="The excerpt the model quoted.")
    value: Any = Field(
        description=(
            "The checked value, applied to the document (`ok` only): an id, a list of tag ids, "
            "a date, or a field value."
        )
    )
    suggestion: Any = Field(
        description="A value that can be accepted as it is (uncertain fields, and the topic)."
    )
    new_name: str | None = Field(
        description="A contact or document type that does not exist; an admin can create it."
    )
    quote_shows_value: bool | None = Field(
        default=None,
        description=(
            "Whether the quote itself shows the proposed value (the contact's name, the date, "
            "the amount); false if it only leads to it (a customer number for the contact); "
            "null without a quote."
        ),
    )
    locations: list[LocationOut] = Field(
        default_factory=list,
        description=(
            "Where the quote stands in the archive PDF: at most a few places, in page order; "
            "empty if it was not found there or the check is older than this."
        ),
    )
    candidates: list[CandidateOut] = Field(
        default_factory=list,
        description=(
            "Contact, document type, topic and tags: the values the signals proposed, best "
            "first, with what each signal said; empty for other fields and older checks."
        ),
    )

    @classmethod
    def of(cls, check: FieldCheck) -> "FieldCheckOut":
        return cls(
            field=check.field,
            outcome=check.outcome,
            confidence=round(check.confidence, 4),
            reason=check.reason,
            proposed=check.proposed,
            evidence=check.evidence,
            value=check.value,
            suggestion=check.suggestion,
            new_name=check.new_name,
            quote_shows_value=check.quote_shows_value,
            locations=[LocationOut.of(location) for location in check.locations],
            candidates=[CandidateOut.of(candidate) for candidate in check.candidates],
        )


class OpenNodeOut(BaseModel):
    """A node that was uncertain or failed, with the fields left to decide."""

    node: Node
    outcome: Outcome
    reason: str | None
    fields: list[FieldCheckOut] = Field(
        description=(
            "Uncertain fields of classification, field extraction and the rules (also "
            "`space`, `title`, `review`), and of filing (`space`)."
        )
    )

    @classmethod
    def of(cls, item: OpenNode) -> "OpenNodeOut":
        return cls(
            node=item.node,
            outcome=item.outcome,
            reason=item.reason,
            fields=[FieldCheckOut.of(check) for check in item.fields],
        )


class InboxItemOut(BaseModel):
    document: DocumentDetails
    open: list[OpenNodeOut]

    @classmethod
    def of(cls, item: InboxItem) -> "InboxItemOut":
        return cls(
            document=DocumentDetails.of(item.document, ShareLevel.READ_WRITE),
            open=[OpenNodeOut.of(node) for node in item.open],
        )


class InboxPage(BaseModel):
    items: list[InboxItemOut]
    next_cursor: str | None = Field(
        description="Pass as `cursor` for the next page; null on the last page."
    )


class NodeReviewOut(BaseModel):
    """The latest run of classification or field extraction by the model."""

    node: Node
    run: int
    outcome: Outcome
    reason: str | None
    model_version: str | None = Field(examples=["qwen3:8b"])
    truncated: bool | None = Field(description="Whether a part of the text was left out.")
    fields: list[FieldCheckOut]

    @classmethod
    def of(cls, review: NodeReview) -> "NodeReviewOut":
        return cls(
            node=review.node,
            run=review.run,
            outcome=review.outcome,
            reason=review.reason,
            model_version=review.model_version,
            truncated=review.truncated,
            fields=[FieldCheckOut.of(check) for check in review.fields],
        )


class ReviewOut(BaseModel):
    document: DocumentDetails
    open: list[OpenNodeOut]
    nodes: list[NodeReviewOut]

    @classmethod
    def of(cls, review: Review) -> "ReviewOut":
        return cls(
            document=DocumentDetails.of(review.document, ShareLevel.READ_WRITE),
            open=[OpenNodeOut.of(node) for node in review.open],
            nodes=[NodeReviewOut.of(node) for node in review.nodes],
        )


class ConfirmRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "changes": {"document_date": "2026-03-31"},
                    "accept_suggestions": True,
                    "resume_at": "apply_rules",
                }
            ]
        },
    )

    changes: DocumentPatch = Field(
        default_factory=DocumentPatch,
        description="A metadata change as with PATCH; decides the fields it sets.",
    )
    accept_suggestions: bool = Field(
        default=False,
        description=(
            "Take the suggestion of every open field that is neither in `changes` nor set on "
            "the document."
        ),
    )
    resume_at: ResumeNode = Field(
        default=ResumeNode.APPLY_RULES,  # type: ignore[attr-defined]
        description=(
            "`fields` after correcting the document type, so the fields of the new type are read."
        ),
    )
    space_id: UUID | None = Field(
        default=None,
        description=(
            "Move the document into this space (one the owner may write to); decides the "
            "rules' open field `space`. Default: it stays where it is."
        ),
    )
