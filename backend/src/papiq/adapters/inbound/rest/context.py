"""What the API needs from the rest of the application, handed over by the composition root."""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated

from fastapi import Depends, Request

from papiq.core.ports import EventBus
from papiq.core.services.auth import AuthService
from papiq.core.services.documents import DocumentService
from papiq.core.services.drawers import DrawerService
from papiq.core.services.indexing import IndexingService
from papiq.core.services.master_data import MasterDataService
from papiq.core.services.oidc import OidcService
from papiq.core.services.pipeline import PipelineService
from papiq.core.services.rules import RuleService
from papiq.core.services.search import SearchService
from papiq.core.services.users import UserService

type HealthCheck = Callable[[], Awaitable[None]]


@dataclass(frozen=True)
class ApiContext:
    auth: AuthService
    users: UserService
    drawers: DrawerService
    master_data: MasterDataService
    pipeline: PipelineService
    documents: DocumentService
    rules: RuleService
    event_bus: EventBus
    health_checks: Mapping[str, HealthCheck]
    max_upload_size: int
    max_request_size: int = 1024 * 1024  # every other request body
    # Checks of parts the API works without (the search): a failure is `degraded`, not `503`.
    optional_checks: frozenset[str] = frozenset()
    oidc: OidcService | None = None
    # Both with a search index; without, the search endpoints answer 503.
    search: SearchService | None = None
    indexing: IndexingService | None = None
    events_poll_interval: timedelta = timedelta(seconds=1)
    # `False` only for development over plain HTTP: cookies without `Secure`.
    cookie_secure: bool = True
    # Event streams check every so often that their session or token is still valid.
    stream_recheck_interval: timedelta = timedelta(seconds=30)


def _context(request: Request) -> ApiContext:
    context: ApiContext = request.app.state.context
    return context


Context = Annotated[ApiContext, Depends(_context)]
