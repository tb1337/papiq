"""Search: `GET /documents/search` (words and meaning in one call) and `POST /search/reindex`."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response

from papiq.adapters.inbound.rest.auth import PROTECTED, CurrentUser
from papiq.adapters.inbound.rest.context import Context
from papiq.adapters.inbound.rest.documents import LaneFilter, document_filter
from papiq.adapters.inbound.rest.problems import problem_responses
from papiq.adapters.inbound.rest.schemas import (
    DocumentDetails,
    SearchResultItem,
    SearchResultPage,
    SnippetSegment,
)
from papiq.core.domain.errors import SearchUnavailableError
from papiq.core.ports.search_index import MAX_HITS, MAX_LIMIT

router = APIRouter(tags=["search"], dependencies=PROTECTED)


@router.get(
    "/documents/search",
    summary="Search documents",
    description=(
        "Full text and, if the installation has embeddings, meaning in one call. Finds the "
        "documents the caller may read, the same ones as `GET /documents` lists, with the "
        "same filters (they combine; `tag_id` and `lane` may repeat). Each hit is checked "
        "against the permissions once more before it is returned. The index follows the "
        "documents a moment later, so a new document may take a few seconds to turn up. "
        "Pages are by `offset`; at most 1000 hits can be reached."
    ),
    response_model=SearchResultPage,
    responses=problem_responses(401, 422, 503),
)
async def search_documents(
    user: CurrentUser,
    context: Context,
    q: Annotated[str, Query(min_length=1, max_length=500, description="What to look for.")],
    contact_id: UUID | None = None,
    document_type_id: UUID | None = None,
    tag_id: Annotated[list[UUID] | None, Query(max_length=50)] = None,
    drawer_id: UUID | None = None,
    lane: Annotated[list[LaneFilter] | None, Query(max_length=4)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 20,
    offset: Annotated[int, Query(ge=0, le=MAX_HITS)] = 0,
    semantic_ratio: Annotated[
        float | None,
        Query(
            ge=0,
            le=1,
            description="Weight of the meaning against the words: 0 words only, 1 meaning "
            "only. Default: the installation's setting.",
        ),
    ] = None,
) -> SearchResultPage:
    if context.search is None:
        raise SearchUnavailableError("search is not configured")
    page = await context.search.search(
        user,
        q,
        document_filter(contact_id, document_type_id, tag_id, drawer_id, lane),
        offset=offset,
        limit=limit,
        semantic_ratio=semantic_ratio,
    )
    return SearchResultPage(
        items=[
            SearchResultItem(
                document=DocumentDetails.of(item.document, item.access),
                score=item.score,
                snippet=[SnippetSegment(text=s.text, match=s.match) for s in item.snippet],
            )
            for item in page.items
        ],
        estimated_total=page.estimated_total,
        next_offset=page.next_offset,
        semantic=page.semantic,
    )


@router.post(
    "/search/reindex",
    status_code=202,
    summary="Rebuild the search index",
    description=(
        "Admins only. Builds a new index from the database and the stored files next to the "
        "active one and swaps it in when it is complete; the search keeps working meanwhile. "
        "Runs in the background and may take long; a rebuild that is queued or running is not "
        "queued twice."
    ),
    response_class=Response,
    responses={202: {"description": "Queued."}, **problem_responses(401, 403, 503)},
)
async def reindex(user: CurrentUser, context: Context) -> Response:
    if context.indexing is None:
        raise SearchUnavailableError("search is not configured")
    await context.indexing.request_rebuild(user)
    return Response(status_code=202)
