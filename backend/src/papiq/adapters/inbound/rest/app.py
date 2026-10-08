"""The FastAPI application. All routes live below `/api/v1`; `/` stays free for the web UI."""

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute, iter_route_contexts

from papiq import __version__
from papiq.adapters.inbound.rest import (
    account,
    documents,
    drawers,
    health,
    master_data,
    problems,
    rules,
    search,
    streams,
    users,
    webhooks,
)
from papiq.adapters.inbound.rest.auth import (
    SECURITY,
    SECURITY_SCHEMES,
    SESSION_ONLY,
    authenticate,
    session_principal,
)
from papiq.adapters.inbound.rest.context import ApiContext
from papiq.adapters.inbound.rest.events import EventHub, dispatch_forever
from papiq.adapters.inbound.rest.middleware import LimitRequestBody, NoStore
from papiq.adapters.inbound.rest.schemas import EventMessage

PREFIX = "/api/v1"

DESCRIPTION = """\
Papiq is a headless document management system; this API is the only way in.

Errors are problem details (RFC 9457, `application/problem+json`).

Authentication: a session cookie from `POST /auth/login` (web UI; changing requests also send
the header `X-CSRF-Token`) or a personal API token as `Authorization: Bearer papiq_…`. Every
endpoint needs one of them (401 without), except health and the sign-in endpoints: `POST
/auth/login` and, below `/auth/oidc`, information, start and callback. A `read` token may only
read (403). The own sign-in (password, sessions, TOTP, API tokens, provider links) and
creating users or resetting their sign-in need a session; a token gets 403 there. Documents
and drawers the caller may not see are "not found" (404), the same as ones that do not exist.
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
    app.add_middleware(
        LimitRequestBody,
        limit=context.max_request_size,
        exempt={("POST", f"{PREFIX}/documents")},  # the upload has its own limit
    )
    app.add_middleware(NoStore, prefix=f"{PREFIX}/auth")
    for router in (
        account.public,
        account.router,
        users.router,
        *master_data.ROUTERS,
        drawers.router,
        rules.router,
        rules.applications,
        search.router,  # before documents: `/documents/search` is no document id
        documents.router,
        documents.inbox,
        streams.router,
        webhooks.router,
        health.router,
    ):
        app.include_router(router, prefix=PREFIX)

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
        schema["components"]["securitySchemes"] = SECURITY_SCHEMES
        for context in iter_route_contexts(app.routes):
            route = context.original_route
            if isinstance(route, APIRoute) and route.include_in_schema:
                security = security_of(route)
                for method in context.methods or ():
                    operation = schema["paths"][context.path_format][method.lower()]
                    operation["security"] = security
                    responses = operation["responses"]
                    if "requestBody" in operation and "413" not in responses:
                        responses["413"] = problems.problem_responses(413)[413]
                    # A `read` token or a missing CSRF header is refused on every change; a
                    # token wherever a session is needed.
                    refusable = security == SESSION_ONLY or (security and method != "GET")
                    if refusable and "403" not in responses:
                        responses["403"] = problems.problem_responses(403)[403]
        return schema

    app.openapi = openapi  # type: ignore[method-assign]
    return app


def security_of(route: APIRoute) -> list[dict[str, list[str]]]:
    """From the route's dependencies: public (none), session only, or session or token."""
    calls = set(_calls(route.dependant))
    if session_principal in calls:
        return SESSION_ONLY
    if authenticate in calls:
        return SECURITY
    return []


def _calls(dependant: Dependant) -> Any:
    for dependency in dependant.dependencies:
        yield dependency.call
        yield from _calls(dependency)


def close_event_streams(app: FastAPI) -> None:
    """End all open event streams, so that the server can shut down at once."""
    hub: EventHub = app.state.hub
    hub.close()
