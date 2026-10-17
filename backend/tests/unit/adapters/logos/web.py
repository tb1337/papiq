"""A stand-in for the web, for the logo fetcher: websites by host name, a fixed resolver for
their names, and a transport (`httpx2.MockTransport`) that answers instead of the network.

The transport checks what the guard promises for every request it gets: it goes to an address
the resolver gave for the name in `Host` (never resolved anew), with that name for TLS (SNI),
over HTTPS to the default port, without cookies. Nothing leaves the process."""

import asyncio
import gzip
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, field

import httpx2

PUBLIC = "93.184.216.34"
OTHER_PUBLIC = "2606:4700:4700::1111"

type Page = Callable[[httpx2.Request], httpx2.Response]


class Body(httpx2.AsyncByteStream):
    """A body in chunks, each after `delay` seconds."""

    def __init__(self, chunks: Sequence[bytes], delay: float = 0) -> None:
        self._chunks = chunks
        self._delay = delay

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            if self._delay:
                await asyncio.sleep(self._delay)
            yield chunk

    async def aclose(self) -> None:
        pass


def answer(
    content_type: str,
    body: bytes,
    *,
    status: int = 200,
    headers: dict[str, str] | None = None,
    delay: float = 0,
    chunk: int = 64 * 1024,
) -> Page:
    def page(request: httpx2.Request) -> httpx2.Response:
        parts = [body[start : start + chunk] for start in range(0, len(body), chunk)] or [b""]
        return httpx2.Response(
            status,
            headers={"content-type": content_type, **(headers or {})},
            stream=Body(parts, delay),
        )

    return page


def html(text: str, **options: object) -> Page:
    return answer("text/html; charset=utf-8", text.encode(), **options)  # type: ignore[arg-type]


def gzipped(content_type: str, body: bytes) -> Page:
    return answer(content_type, gzip.compress(body), headers={"content-encoding": "gzip"})


def redirect(location: str, status: int = 302) -> Page:
    return answer("text/plain", b"", status=status, headers={"location": location})


@dataclass
class Web:
    dns: dict[str, list[str]] = field(default_factory=dict)
    pages: dict[tuple[str, str], Page] = field(default_factory=dict)
    requests: list[httpx2.Request] = field(default_factory=list)
    lookups: list[str] = field(default_factory=list)
    # What the resolver says next for a name, before `dns` (DNS rebinding).
    next_answers: dict[str, list[list[str]]] = field(default_factory=dict)
    connected: dict[str, list[str]] = field(default_factory=dict)  # host: addresses used

    def site(self, host: str, *addresses: str) -> None:
        self.dns[host] = list(addresses or (PUBLIC,))

    def serve(self, host: str, path: str, page: Page) -> None:
        self.pages[(host, path)] = page

    async def resolve(self, host: str) -> list[str]:
        self.lookups.append(host)
        queued = self.next_answers.get(host)
        if queued:
            return queued.pop(0)
        if host not in self.dns:
            raise OSError(f"{host}: name or service not known")
        return self.dns[host]

    async def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        host = request.headers["host"].strip("[]")
        assert request.extensions.get("sni_hostname") == host
        assert request.url.scheme == "https" and request.url.port is None
        assert "cookie" not in request.headers
        self.connected.setdefault(host, []).append(request.url.host)
        page = self.pages.get((host, request.url.path))
        if page is None:
            return httpx2.Response(404, headers={"content-type": "text/html"}, text="no")
        return page(request)

    @property
    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self.handle)

    def hosts(self) -> list[str]:
        return [request.headers["host"].strip("[]") for request in self.requests]
