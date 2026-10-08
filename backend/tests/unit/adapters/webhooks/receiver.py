"""A webhook receiver on the loopback interface: a minimal HTTP server that records requests."""

import asyncio
import socket
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Received:
    method: str
    path: str
    headers: dict[str, str]  # names in lower case
    body: bytes


def default_answer(received: Received) -> tuple[int, dict[str, str]]:
    """The paths of `WebhookSenderContract`."""
    if received.path == "/redirect":
        return 302, {"Location": "/status/200"}
    if received.path.startswith("/status/"):
        return int(received.path.rsplit("/", 1)[1]), {}
    return 200, {}


class LocalReceiver:
    """Use as `async with LocalReceiver() as receiver`. `answer` maps a request to a status code
    and headers; the path `/slow` is never answered."""

    def __init__(
        self, answer: Callable[[Received], tuple[int, dict[str, str]]] = default_answer
    ) -> None:
        self.received: list[Received] = []
        self._answer = answer
        self._server: asyncio.Server | None = None
        self._port = 0

    async def __aenter__(self) -> "LocalReceiver":
        self._server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        self._port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc: object) -> None:
        assert self._server is not None
        self._server.close()
        self._server.close_clients()
        await self._server.wait_closed()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._port}"

    @property
    def unreachable_url(self) -> str:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        return f"http://127.0.0.1:{port}"  # closed again: nobody listens

    def hits(self, path: str) -> int:
        return Counter(r.path for r in self.received)[path]

    def last_body(self) -> bytes:
        return self.received[-1].body

    def last_headers(self) -> dict[str, str]:
        return self.received[-1].headers

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = (await reader.readuntil(b"\r\n\r\n")).decode("latin-1").split("\r\n")
            method, path, _ = head[0].split(" ", 2)
            headers = {}
            for line in head[1:]:
                if ":" in line:
                    name, value = line.split(":", 1)
                    headers[name.strip().lower()] = value.strip()
            body = await reader.readexactly(int(headers.get("content-length", "0")))
            received = Received(method, path, headers, body)
            self.received.append(received)
            if path == "/slow":
                await asyncio.sleep(30)
                return
            status, extra = self._answer(received)
            lines = [f"HTTP/1.1 {status} Test", "Content-Length: 2", "Connection: close"]
            lines += [f"{name}: {value}" for name, value in extra.items()]
            writer.write(("\r\n".join(lines) + "\r\n\r\nok").encode())
            await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, asyncio.CancelledError):
            pass
        finally:
            writer.close()
