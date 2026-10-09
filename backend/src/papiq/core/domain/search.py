"""What the search index holds, and who may find it.

The index is derived from the repository and the object store and can always be rebuilt from
them. `Visibility.allows` is the reach rule of `permissions.py` in the form of an index document:
a test keeps both in line, and every search needs a `Visibility`.
"""

from dataclasses import dataclass, field
from datetime import date, datetime

from papiq.core.domain.ids import (
    ContactId,
    DocumentId,
    DocumentTypeId,
    DrawerId,
    TagId,
    UserId,
)
from papiq.core.domain.pipeline import Lane

PROCESSING = "processing"
"""The lane value of a document that has none yet."""


def lane_value(lane: Lane | None) -> str:
    return PROCESSING if lane is None else lane.value


@dataclass(frozen=True)
class EmbeddingStamp:
    """Which model computed the vectors of a document, from which text: `digest` covers the
    texts that were embedded. Vectors whose stamp differs from the current one are stale."""

    model: str
    digest: str


@dataclass(frozen=True, kw_only=True)
class IndexDocument:
    """A document as the index holds it. Names are indexed for the search, ids for the filters.

    `vectors` holds one vector per section of the text (all of one length); `embedding` says
    how they were made and is required with them. `vectors=None` keeps the vectors (and their
    stamp) that the index already holds for the document: for an update after which the text,
    and so the vectors, did not change.
    """

    id: DocumentId
    version: int
    owner_id: UserId
    drawer_id: DrawerId
    lane: Lane | None
    title: str
    filename: str
    text: str = ""
    contact_id: ContactId | None = None
    contact: str | None = None
    document_type_id: DocumentTypeId | None = None
    document_type: str | None = None
    tag_ids: tuple[TagId, ...] = ()
    tags: tuple[str, ...] = ()
    fields: tuple[str, ...] = ()
    document_date: date | None = None
    created_at: datetime
    vectors: tuple[tuple[float, ...], ...] | None = field(default=(), repr=False)
    embedding: EmbeddingStamp | None = None

    def __post_init__(self) -> None:
        if self.vectors is None:
            if self.embedding is not None:
                raise ValueError("kept vectors keep their stamp: give none")
            return
        if self.vectors and self.embedding is None:
            raise ValueError("vectors need an embedding stamp")
        if len({len(vector) for vector in self.vectors}) > 1 or any(
            not vector for vector in self.vectors
        ):
            raise ValueError("vectors must be non-empty and of one length")

    @property
    def lane_value(self) -> str:
        return lane_value(self.lane)


@dataclass(frozen=True)
class IndexState:
    """What the index knows about a document, without the content."""

    id: DocumentId
    version: int
    embedding: EmbeddingStamp | None


@dataclass(frozen=True)
class Visibility:
    """Who is searching and which drawers they can read from: the drawers they own and those
    shared with them. Own documents are visible in every lane; other users' documents only when
    green and in one of `drawers` (the user's reach, `permissions.in_reach`). `everything`: every
    document, for an admin who asks for all users' documents."""

    user: UserId
    drawers: frozenset[DrawerId]
    everything: bool = False

    def allows(self, document: IndexDocument) -> bool:
        if self.everything or document.owner_id == self.user:
            return True
        return document.lane is Lane.GREEN and document.drawer_id in self.drawers


@dataclass(frozen=True)
class Segment:
    """A piece of text of a hit, marked if it matched the query."""

    text: str
    match: bool = False
