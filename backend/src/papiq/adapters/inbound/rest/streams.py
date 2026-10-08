"""`GET /events`: server-sent events for the caller's documents."""

from collections.abc import AsyncIterable
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.sse import EventSourceResponse, ServerSentEvent

from papiq.adapters.inbound.rest.auth import PROTECTED, CurrentUser, still_authenticated
from papiq.adapters.inbound.rest.context import Context
from papiq.adapters.inbound.rest.events import EventHub, message
from papiq.adapters.inbound.rest.problems import problem_responses
from papiq.core.domain.ids import DocumentId

router = APIRouter(tags=["events"], dependencies=PROTECTED)


def _hub(request: Request) -> EventHub:
    hub: EventHub = request.app.state.hub
    return hub


async def _visible_document(
    user: CurrentUser,
    context: Context,
    document_id: Annotated[UUID | None, Query(description="Only events of this document.")] = None,
) -> DocumentId | None:
    """Checked before the stream starts: 404 if the caller may not see the document."""
    if document_id is None:
        return None
    await context.documents.get(user, DocumentId(document_id))
    return DocumentId(document_id)


@router.get(
    "/events",
    summary="Progress of documents (server-sent events)",
    description=(
        "A stream of document events (`document.received`, `document.step_completed`, "
        "`document.lane_changed`, `document.filed`, `document.updated`, `document.deleted`), "
        "only for documents the caller may read at that moment (`document.deleted`: could "
        "read when it was deleted); other users' documents are visible once green. "
        "Each event has the event type as `event`, the event id as `id`, and JSON as `data` "
        "(see `EventMessage`). Events are thin: fetch the document for its state. There is "
        "no replay; after reconnecting, fetch the current state. The stream checks every "
        "30 seconds that its session or token is still valid and ends otherwise."
    ),
    response_class=EventSourceResponse,
    responses={
        200: {
            "description": "Event stream",
            "content": {
                "text/event-stream": {
                    "example": (
                        "event: document.step_completed\n"
                        "id: 01999d5f-1b2c-7d3e-8f40-5a6b7c8d9e0f\n"
                        'data: {"type": "document.step_completed", '
                        '"id": "01999d5f-1b2c-7d3e-8f40-5a6b7c8d9e0f", '
                        '"occurred_at": "2026-10-07T08:00:00Z", '
                        '"document_id": "01999d5e-8a7f-7c1e-b6a3-2f4d5e6f7a8b", '
                        '"step": "ocr", "run": 1, "outcome": "ok"}\n\n'
                    ),
                }
            },
        },
        **problem_responses(401, 404, 422),
    },
)
async def events(
    request: Request,
    user: CurrentUser,
    context: Context,
    hub: Annotated[EventHub, Depends(_hub)],
    document: Annotated[DocumentId | None, Depends(_visible_document)],
) -> AsyncIterable[ServerSentEvent]:
    async def still_allowed() -> bool:
        return await still_authenticated(request, context, user)

    async with hub.listen(user, document) as listener:
        async for event in listener.events(still_allowed, context.stream_recheck_interval):
            yield ServerSentEvent(event=event.type, id=str(event.id), data=message(event))
