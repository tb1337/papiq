"""The FastAPI application. All routes live below `/api/v1`; `/` stays free for the web UI."""

import asyncio
import contextlib
from collections.abc import AsyncIterator

from fastapi import FastAPI

from papiq import __version__
from papiq.adapters.inbound.rest import documents, health, problems, streams
from papiq.adapters.inbound.rest.context import ApiContext
from papiq.adapters.inbound.rest.events import EventHub, dispatch_forever
from papiq.adapters.inbound.rest.schemas import EventMessage

PREFIX = "/api/v1"

DESCRIPTION = """\
Papiq is a headless document management system; this API is the only way in.

Errors are problem details (RFC 9457, `application/problem+json`). Authentication follows in
M4: until then every request that needs a user is answered with 401.
"""


EVENT_ITEM = {
    "type": "object",
    "required": ["event", "id", "data"],
    "properties": {
        "event": {"type": "string", "description": "The event type, as in `data.type`."},
        "id": {"type": "string", "description": "The event id, as in `data.id`."},
        "data": {
            "type": "string",
            "contentMediaType": "application/json",
            "contentSchema": {"$ref": "#/components/schemas/EventMessage"},
        },
    },
}


def create_app(context: ApiContext) -> FastAPI:
    """The API on the given services. While the app runs (lifespan), it delivers outbox events
    to its event streams."""
    hub = EventHub(context.documents)
    hub.attach(context.event_bus)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        dispatcher = asyncio.create_task(
            dispatch_forever(context.event_bus, context.events_poll_interval)
        )
        try:
            yield
        finally:
            hub.close()
            dispatcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await dispatcher

    app = FastAPI(
        title="Papiq API",
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
        openapi_url=f"{PREFIX}/openapi.json",
        docs_url=f"{PREFIX}/docs",
        redoc_url=None,
    )
    app.state.context = context
    app.state.hub = hub
    problems.install(app)
    for module in (documents, streams, health):
        app.include_router(module.router, prefix=PREFIX)

    original_openapi = app.openapi

    def openapi() -> dict[str, object]:
        schema = original_openapi()
        components = schema.setdefault("components", {}).setdefault("schemas", {})
        components.setdefault("EventMessage", EventMessage.model_json_schema())
        components.setdefault("Problem", problems.Problem.model_json_schema())
        # The stream: a string for OpenAPI 3.1 tools, `itemSchema` (OpenAPI 3.2) per event,
        # whose `data` is the JSON of an EventMessage.
        stream = schema["paths"][f"{PREFIX}/events"]["get"]["responses"]["200"]["content"]
        stream["text/event-stream"] = {
            "schema": {"type": "string", "description": "Server-sent events"},
            "itemSchema": EVENT_ITEM,
            "example": stream["text/event-stream"]["example"],
        }
        # Validation errors are problems too; FastAPI's own schemas for them are unused.
        for unused in ("HTTPValidationError", "ValidationError"):
            components.pop(unused, None)
        return schema

    app.openapi = openapi  # type: ignore[method-assign]
    return app


def close_event_streams(app: FastAPI) -> None:
    """End all open event streams, so that the server can shut down at once."""
    hub: EventHub = app.state.hub
    hub.close()
