"""Reachability probes for integration tests. Standard library only, no client libraries."""

import json
import socket
import urllib.error
import urllib.request

TIMEOUT = 3.0

# Postgres answers an SSLRequest with a single byte: 'S' (supported) or 'N' (not supported).
_POSTGRES_SSL_REQUEST = (8).to_bytes(4, "big") + (80877103).to_bytes(4, "big")

# Requests to the services of the compose network must never go through a configured proxy.
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def postgres_answers(host: str, port: int) -> bool:
    """True if a Postgres server speaks on host:port."""
    try:
        with socket.create_connection((host, port), timeout=TIMEOUT) as connection:
            connection.sendall(_POSTGRES_SSL_REQUEST)
            return connection.recv(1) in {b"S", b"N"}
    except OSError:
        return False


def http_status(url: str) -> int | None:
    """HTTP status of a GET request, also for error statuses; None if nothing answers."""
    try:
        with _opener.open(url, timeout=TIMEOUT) as response:
            return int(response.status)
    except urllib.error.HTTPError as error:
        return error.code
    except OSError:  # includes URLError and timeouts
        return None


def meilisearch_health(url: str) -> str | None:
    """The `status` reported by Meilisearch's `/health` endpoint; None if unreachable."""
    try:
        with _opener.open(url.rstrip("/") + "/health", timeout=TIMEOUT) as response:
            status = json.load(response).get("status")
    except (OSError, ValueError):
        return None
    return status if isinstance(status, str) else None
