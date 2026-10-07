"""REST API (FastAPI). The OpenAPI specification is the contract for all clients."""

from papiq.adapters.inbound.rest.app import PREFIX, close_event_streams, create_app
from papiq.adapters.inbound.rest.auth import current_user
from papiq.adapters.inbound.rest.context import ApiContext, HealthCheck

__all__ = [
    "PREFIX",
    "ApiContext",
    "HealthCheck",
    "close_event_streams",
    "create_app",
    "current_user",
]
