"""The httpx2 sender against a receiver on the loopback interface: the contracts, and what the
contract cannot say (the headers it adds, certificate errors)."""

from collections.abc import AsyncIterator
from datetime import timedelta

import pytest

from papiq import __version__
from papiq.adapters.outbound.webhooks import HttpWebhookSender
from papiq.core.ports.webhook_sender import WebhookRequest, WebhookSender
from tests.contracts.webhook_sender import Receiver, WebhookSenderContract, request
from tests.unit.adapters.webhooks.receiver import LocalReceiver


class TestHttpWebhookSender(WebhookSenderContract):
    @pytest.fixture
    def sender(self) -> WebhookSender:
        return HttpWebhookSender(user_agent=f"Papiq/{__version__}")

    @pytest.fixture
    async def receiver(self) -> AsyncIterator[Receiver]:
        async with LocalReceiver() as receiver:
            yield receiver

    async def test_sends_a_post_with_a_user_agent(
        self, sender: WebhookSender, receiver: LocalReceiver
    ) -> None:
        await sender.send(request(f"{receiver.url}/status/200"))
        sent = receiver.received[-1]
        assert sent.method == "POST"
        assert sent.headers["user-agent"] == f"Papiq/{__version__}"

    async def test_the_timeout_covers_the_whole_exchange(
        self, sender: WebhookSender, receiver: LocalReceiver
    ) -> None:
        response = await sender.send(request(f"{receiver.url}/slow", timeout=0.3))
        assert response.error == "no answer within 0.3 seconds"
        assert response.duration_ms < 3000

    async def test_a_certificate_error_is_named_without_the_url(
        self, receiver: LocalReceiver
    ) -> None:
        # The receiver speaks plain HTTP: the TLS handshake fails.
        sender = HttpWebhookSender()
        response = await sender.send(
            request(receiver.url.replace("http://", "https://") + "/hooks/token")
        )
        assert response.status_code is None
        assert response.error
        assert "token" not in response.error

    async def test_an_unusable_address_is_an_error_too(self, sender: WebhookSender) -> None:
        response = await sender.send(
            WebhookRequest(
                url="http://[::1:99999/x",
                headers={},
                body=b"{}",
                timeout=timedelta(seconds=1),
            )
        )
        assert response.status_code is None
        assert response.error
