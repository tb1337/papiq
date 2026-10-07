"""Search index: full text and vectors in one index.

Derived data: it can always be rebuilt from the repository and the object store, and it lags
behind them a little (updates run as jobs). Every search needs a `Visibility`; the core also
checks every hit against the repository before showing it. First adapter: Meilisearch.
"""

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from papiq.core.domain.ids import DocumentId
from papiq.core.domain.search import IndexDocument, IndexState, Segment, Visibility
from papiq.core.ports.repository import DocumentFilter

MAX_LIMIT = 100
MAX_HITS = 1000
"""Hits a search can reach: `offset + limit` may not be larger."""


@dataclass(frozen=True, kw_only=True)
class SearchQuery:
    """`visibility` has no default: a query cannot be built without the rights filter.

    `vector`: the embedding of `text`; without it the search is plain full text. With it,
    `semantic_ratio` (0 to 1) weighs the vectors against the words.
    """

    text: str
    visibility: Visibility
    filter: DocumentFilter = field(default_factory=DocumentFilter)
    vector: tuple[float, ...] | None = None
    semantic_ratio: float = 0.5
    offset: int = 0
    limit: int = 20

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("the query text is empty")
        if not 0 <= self.semantic_ratio <= 1:
            raise ValueError("semantic_ratio must be between 0 and 1")
        if self.offset < 0 or not 1 <= self.limit <= MAX_LIMIT:
            raise ValueError(f"offset must be 0 or more and limit between 1 and {MAX_LIMIT}")
        if self.offset + self.limit > MAX_HITS:
            raise ValueError(f"offset + limit may not exceed {MAX_HITS}")


@dataclass(frozen=True, kw_only=True)
class SearchHit:
    id: DocumentId
    version: int
    score: float | None  # 0 to 1, higher is better; None if the index gives none
    snippet: tuple[Segment, ...]  # from the text, around the first match


@dataclass(frozen=True, kw_only=True)
class SearchResult:
    hits: list[SearchHit]  # best first
    estimated_total: int  # an upper bound, not exact
    semantic: bool  # whether vectors took part


class IndexBuild(Protocol):
    """A new index being filled next to the active one, which keeps serving searches."""

    async def add(self, documents: Sequence[IndexDocument]) -> None: ...

    async def finish(self) -> None:
        """The new index replaces the active one; the old one is removed."""
        ...

    async def abort(self) -> None:
        """Drop the new index; the active one stays as it is."""
        ...


class SearchIndex(Protocol):
    """Errors: SearchUnavailableError if the index cannot be reached or does not answer in time,
    SearchIndexError if it refuses a request or a task fails. Writes return once the change is
    visible to searches."""

    async def upsert(self, documents: Sequence[IndexDocument]) -> None:
        """Add the documents or replace those with the same id."""
        ...

    async def remove(self, id: DocumentId) -> None:
        """Does nothing if the document is not in the index."""
        ...

    async def state(self, id: DocumentId) -> IndexState | None: ...

    def states(self) -> AsyncIterator[IndexState]:
        """All documents in the index, in no particular order."""
        ...

    async def search(self, query: SearchQuery) -> SearchResult:
        """Documents that match the text and the filter and that `query.visibility` allows,
        best first, from `offset` on, at most `limit`."""
        ...

    async def begin_rebuild(self) -> IndexBuild:
        """Start a new index, discarding the remains of an aborted rebuild."""
        ...

    async def check(self) -> None:
        """Raise SearchUnavailableError unless the index is reachable and healthy."""
        ...
