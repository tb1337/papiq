"""The MCP endpoint of the API: Streamable HTTP, stateless, with bearer tokens.

- **Stateless**: every request stands alone, there are no sessions in the process and a token is
  not tied to one. Answers are JSON, not event streams.
- **Bearer tokens only**: the same personal API tokens as for the REST API (`read` may read, the
  tool that changes needs `read_write`). Cookies and sessions do not count. Without a valid
  token the answer is a 401 problem with `WWW-Authenticate: Bearer`. The SDK's own OAuth
  support is not used: Papiq announces no authorisation server it does not have.
- **DNS rebinding protection** of the SDK is off: it would only let `localhost` names in. A
  request needs the token in a header, which a page in a browser cannot send to another origin
  without the server's consent, and which it does not know.
- The size of a request is limited by the API's middleware, as for every endpoint.
"""

import contextlib
import logging
from collections.abc import AsyncIterator

from mcp.server.transport_security import TransportSecuritySettings
from starlette.datastructures import Headers
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from papiq.adapters.inbound.mcp.server import build_server
from papiq.adapters.inbound.rest.context import ApiContext
from papiq.adapters.inbound.rest.problems import problem
from papiq.core.domain.errors import AuthenticationError

log = logging.getLogger(__name__)


class BearerAuth:
    """ASGI wrapper: lets a request through only with a valid API token, and hands the caller
    to the tools as `request.state.principal`."""

    def __init__(self, app: ASGIApp, context: ApiContext) -> None:
        self._app = app
        self._context = context

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        try:
            scheme, _, token = Headers(scope=scope).get("authorization", "").partition(" ")
            if scheme.lower() != "bearer" or not token.strip():
                raise AuthenticationError("authentication is required")
            principal = await self._context.auth.authenticate_token(token.strip())
        except AuthenticationError as error:
            response = problem(401, str(error), headers={"WWW-Authenticate": "Bearer"})
            await response(scope, receive, send)
            return
        scope.setdefault("state", {})["principal"] = principal
        await self._app(scope, receive, send)


class _Forward:
    """ASGI endpoint: passes the request on with the path the server listens on."""

    def __init__(self, app: ASGIApp, path: str) -> None:
        self._app = app
        self._path = path

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self._app(
            {**scope, "path": self._path, "raw_path": self._path.encode()}, receive, send
        )


class McpEndpoint:
    """The server, ready to be added to the API: `routes` for the two spellings of its path,
    `run()` around the app's lifetime."""

    def __init__(self, context: ApiContext, *, path: str, text_max: int) -> None:
        self._path = path
        server = build_server(context, text_max=text_max)
        asgi = server.streamable_http_app(
            streamable_http_path=path,
            json_response=True,
            stateless_http=True,
            transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        )
        self._server = server
        self._app = BearerAuth(asgi, context)

    @property
    def routes(self) -> list[Route]:
        """The path as given and with a trailing slash (a redirect would not be followed by
        every client that POSTs)."""
        forward = _Forward(self._app, self._path)
        return [Route(self._path, forward), Route(self._path + "/", forward)]

    @contextlib.asynccontextmanager
    async def run(self) -> AsyncIterator[None]:
        async with self._server.session_manager.run():
            yield
