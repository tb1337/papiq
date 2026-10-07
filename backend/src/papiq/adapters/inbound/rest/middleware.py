"""ASGI middleware of the API."""

from collections.abc import Collection

from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from papiq.adapters.inbound.rest.problems import problem


class RequestTooLargeError(HTTPException):
    """The request body exceeds the limit. An HTTPException, so that FastAPI passes it on
    while it reads a body instead of turning it into 400."""

    def __init__(self, limit: int) -> None:
        super().__init__(413, f"the request body is larger than {limit} bytes")


class LimitRequestBody:
    """Bounds request bodies: by `Content-Length` before anything is read, and by the bytes
    received otherwise. Routes in `exempt` (method, path) have limits of their own, such as the
    upload. FastAPI reads JSON bodies completely before validating them, so without this a
    client could make the process hold any amount of data."""

    def __init__(self, app: ASGIApp, *, limit: int, exempt: Collection[tuple[str, str]]) -> None:
        self._app = app
        self._limit = limit
        self._exempt = frozenset(exempt)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or (scope["method"], scope["path"]) in self._exempt:
            await self._app(scope, receive, send)
            return
        headers = dict(scope["headers"])
        length = headers.get(b"content-length", b"").decode("latin-1")
        if length.isdigit() and int(length) > self._limit:
            error = RequestTooLargeError(self._limit)
            await problem(413, error.detail)(scope, receive, send)
            return
        received = 0

        async def limited() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self._limit:
                    raise RequestTooLargeError(self._limit)
            return message

        await self._app(scope, limited, send)


class NoStore:
    """Marks every answer below `prefix` `Cache-Control: no-store`, also errors and redirects:
    they carry CSRF tokens, TOTP secrets, recovery codes and API tokens, which no cache may
    keep."""

    def __init__(self, app: ASGIApp, *, prefix: str) -> None:
        self._app = app
        self._prefix = prefix.rstrip("/")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if scope["type"] != "http" or not (
            path == self._prefix or path.startswith(self._prefix + "/")
        ):
            await self._app(scope, receive, send)
            return

        async def marked(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["Cache-Control"] = "no-store"
            await send(message)

        await self._app(scope, receive, marked)
