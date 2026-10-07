"""Searching documents: full text and, with embeddings, meaning, in one call.

The index is derived data and lags a little behind the database, so it is a pre-selection only:
the rights are in the query to the index (`Visibility`), and every hit is checked against the
database once more before it is returned (`document_access`), together with the filter. A
share that was withdrawn or a lane that changed since the index was written costs a hit, never
a leak. That is why a page can hold fewer items than `limit`; `next_offset` counts index hits,
so paging over the pages still reaches every hit.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import timedelta

from papiq.core.domain.documents import Document
from papiq.core.domain.drawers import Drawer, ShareLevel
from papiq.core.domain.errors import EmbeddingsError, ValidationError
from papiq.core.domain.ids import DrawerId, UserId
from papiq.core.domain.permissions import document_access
from papiq.core.domain.search import Segment, Visibility
from papiq.core.ports import (
    DocumentFilter,
    Embeddings,
    SearchIndex,
    SearchQuery,
    UnitOfWorkFactory,
)
from papiq.core.ports.search_index import MAX_HITS, MAX_LIMIT
from papiq.core.services._access import load_actor

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SearchPolicy:
    semantic_ratio: float = 0.5  # weight of the meaning against the words, 0 to 1
    embed_timeout: timedelta = timedelta(seconds=5)  # for the query; then plain full text
    query_prefix: str = ""  # put before the query, for models that ask for it

    def __post_init__(self) -> None:
        if not 0 <= self.semantic_ratio <= 1:
            raise ValueError("semantic_ratio must be between 0 and 1")


@dataclass(frozen=True)
class SearchItem:
    document: Document
    access: ShareLevel  # of the caller
    score: float | None
    snippet: tuple[Segment, ...]


@dataclass(frozen=True)
class SearchPage:
    items: list[SearchItem]  # best first; may be fewer than asked for
    estimated_total: int  # an upper bound of the hits, as far as the caller may see them
    next_offset: int | None  # where the next page starts; None after the last one
    semantic: bool  # whether the meaning took part (it does not if embeddings are off or down)


class SearchService:
    def __init__(
        self,
        uow: UnitOfWorkFactory,
        index: SearchIndex,
        embeddings: Embeddings | None = None,
        policy: SearchPolicy | None = None,
    ) -> None:
        self._uow = uow
        self._index = index
        self._embeddings = embeddings
        self._policy = policy or SearchPolicy()

    async def search(
        self,
        actor: UserId,
        text: str,
        filter: DocumentFilter | None = None,
        *,
        offset: int = 0,
        limit: int = 20,
        semantic_ratio: float | None = None,
    ) -> SearchPage:
        """Documents the caller may read that match `text` (and `filter`).

        ValidationError for an empty text or a paging outside the limits, AuthenticationError
        for a deactivated caller, SearchUnavailableError if the index cannot be reached.
        """
        filter = filter or DocumentFilter()
        ratio = self._policy.semantic_ratio if semantic_ratio is None else semantic_ratio
        if not text.strip():
            raise ValidationError("the search text is empty")
        if not 0 <= ratio <= 1:
            raise ValidationError("semantic_ratio must be between 0 and 1")
        if offset < 0 or not 1 <= limit <= MAX_LIMIT:
            raise ValidationError(f"offset must be 0 or more and limit between 1 and {MAX_LIMIT}")
        async with self._uow() as uow:
            user = await load_actor(uow, actor)
            drawers = await uow.drawers.list_accessible(actor)
        if offset >= MAX_HITS:
            return SearchPage([], 0, None, False)
        limit = min(limit, MAX_HITS - offset)

        vector = await self._embed(text) if ratio > 0 else None
        result = await self._index.search(
            SearchQuery(
                text=text,
                visibility=Visibility(actor, frozenset(drawer.id for drawer in drawers)),
                filter=filter,
                vector=vector,
                semantic_ratio=ratio,
                offset=offset,
                limit=limit,
            )
        )

        items = []
        by_id: dict[DrawerId, Drawer] = {drawer.id: drawer for drawer in drawers}
        async with self._uow() as uow:
            for hit in result.hits:
                document = await uow.documents.find(hit.id)
                if document is None:
                    continue  # deleted since the index was written
                if document.drawer_id not in by_id:
                    by_id[document.drawer_id] = await uow.drawers.get(document.drawer_id)
                access = document_access(user, document, by_id[document.drawer_id])
                if access is None or not _matches(document, filter):
                    continue  # the index is behind: a share or a lane has changed
                items.append(SearchItem(document, access, hit.score, hit.snippet))
        if len(items) < len(result.hits):
            log.info(
                "search hits dropped after the check against the database",
                extra={"user_id": str(actor), "dropped": len(result.hits) - len(items)},
            )

        end = offset + len(result.hits)
        more = len(result.hits) == limit and end < min(result.estimated_total, MAX_HITS)
        return SearchPage(items, result.estimated_total, end if more else None, result.semantic)

    async def _embed(self, text: str) -> tuple[float, ...] | None:
        """The vector of the query; None (plain full text) if there are no embeddings or the
        service is down or too slow: the search should not depend on it."""
        if self._embeddings is None:
            return None
        try:
            async with asyncio.timeout(self._policy.embed_timeout.total_seconds()):
                result = await self._embeddings.embed([self._policy.query_prefix + text])
        except (EmbeddingsError, TimeoutError) as error:
            log.warning("searching without meaning, the query was not embedded: %s", error)
            return None
        return tuple(result.vectors[0]) if result.vectors else None


def _matches(document: Document, filter: DocumentFilter) -> bool:
    """The filter as the repository applies it."""
    return (
        (filter.contact is None or document.contact_id == filter.contact)
        and (filter.document_type is None or document.document_type_id == filter.document_type)
        and filter.tags <= document.tag_ids
        and (filter.drawer is None or document.drawer_id == filter.drawer)
        and (filter.lanes is None or document.lane in filter.lanes)
    )
