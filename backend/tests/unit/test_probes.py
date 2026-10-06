import http.server
import socket
import threading
from collections.abc import Callable, Iterator

import pytest

from tests import probes


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/health":
            body, status = b'{"status": "available"}', 200
        else:
            body, status = b"denied", 403
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture
def http_url() -> Iterator[str]:
    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


@pytest.fixture
def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def serve_once(reply: bytes) -> tuple[int, Callable[[], None]]:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)

    def run() -> None:
        connection, _ = listener.accept()
        with connection:
            connection.recv(8)
            connection.sendall(reply)
        listener.close()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return int(listener.getsockname()[1]), lambda: thread.join(timeout=5)


@pytest.mark.parametrize(("reply", "expected"), [(b"S", True), (b"N", True), (b"H", False)])
def test_postgres_answers(reply: bytes, expected: bool) -> None:
    port, join = serve_once(reply)
    assert probes.postgres_answers("127.0.0.1", port) is expected
    join()


def test_postgres_unreachable(free_port: int) -> None:
    assert probes.postgres_answers("127.0.0.1", free_port) is False


def test_http_status_includes_error_statuses(http_url: str) -> None:
    assert probes.http_status(http_url + "/") == 403
    assert probes.http_status(http_url + "/health") == 200


def test_http_status_unreachable(free_port: int) -> None:
    assert probes.http_status(f"http://127.0.0.1:{free_port}") is None


def test_meilisearch_health(http_url: str, free_port: int) -> None:
    assert probes.meilisearch_health(http_url) == "available"
    assert probes.meilisearch_health(f"http://127.0.0.1:{free_port}") is None
