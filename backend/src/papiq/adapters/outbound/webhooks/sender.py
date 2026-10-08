"""Sends webhook requests over httpx2.

- One POST per call, no redirects (a 3xx is the answer: the signature would not hold for
  another target), no retries, no proxy settings from the environment.
- The response body is neither read nor kept; leaving the request closes the connection.
- `https` certificates are verified. The timeout covers the whole exchange; connecting may take
  `connect_timeout` of it.
- Errors name the kind of failure only. The URL stays out of them and out of the log: its path
  may carry a secret of the receiver.
"""

import asyncio
import logging
import ssl
import time

import httpx2

from papiq.core.ports.webhook_sender import WebhookRequest, WebhookResponse

log = logging.getLogger(__name__)

CONNECT_TIMEOUT = 5.0  # seconds


class HttpWebhookSender:
    def __init__(
        self,
        *,
        user_agent: str = "Papiq",
        connect_timeout: float = CONNECT_TIMEOUT,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        """`transport` replaces the network in tests."""
        self._user_agent = user_agent
        self._connect_timeout = connect_timeout
        self._transport = transport

    async def send(self, request: WebhookRequest) -> WebhookResponse:
        limit = request.timeout.total_seconds()
        headers = {"User-Agent": self._user_agent, **request.headers}
        started = time.monotonic()
        status: int | None = None
        error: str | None = None
        try:
            async with asyncio.timeout(limit):
                async with (
                    httpx2.AsyncClient(
                        transport=self._transport,
                        follow_redirects=False,
                        trust_env=False,
                        timeout=httpx2.Timeout(limit, connect=min(self._connect_timeout, limit)),
                    ) as client,
                    client.stream(
                        "POST", request.url, content=request.body, headers=headers
                    ) as response,
                ):
                    status = response.status_code
        except (TimeoutError, httpx2.TimeoutException):
            error = f"no answer within {limit:g} seconds"
        except httpx2.HTTPError as failure:
            error = _describe(failure)
        except (httpx2.InvalidURL, ValueError, OSError) as failure:  # an unusable address
            error = f"cannot send: {type(failure).__name__}"
        duration_ms = int((time.monotonic() - started) * 1000)
        if error is not None:
            log.info("webhook request failed", extra={"error": error})
        return WebhookResponse(status_code=status, duration_ms=duration_ms, error=error)


def _describe(failure: httpx2.HTTPError) -> str:
    cause: BaseException | None = failure
    while cause is not None:
        if isinstance(cause, ssl.SSLCertVerificationError):
            return "the TLS certificate was not accepted"
        if isinstance(cause, ssl.SSLError):
            return "TLS handshake failed"
        cause = cause.__cause__ or cause.__context__
    if isinstance(failure, httpx2.ConnectError):
        return "cannot connect"
    if isinstance(failure, httpx2.TooManyRedirects):
        return "too many redirects"
    return f"request failed: {type(failure).__name__}"
