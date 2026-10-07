"""What the API needs from the rest of the application, handed over by the composition root."""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated

from fastapi import Depends, Request

from papiq.core.ports import EventBus
from papiq.core.services.documents import DocumentService
from papiq.core.services.pipeline import PipelineService

type HealthCheck = Callable[[], Awaitable[None]]


@dataclass(frozen=True)
class ApiContext:
    pipeline: PipelineService
    documents: DocumentService
    event_bus: EventBus
    health_checks: Mapping[str, HealthCheck]
    max_upload_size: int
    events_poll_interval: timedelta = timedelta(seconds=1)


def _context(request: Request) -> ApiContext:
    context: ApiContext = request.app.state.context
    return context


Context = Annotated[ApiContext, Depends(_context)]
