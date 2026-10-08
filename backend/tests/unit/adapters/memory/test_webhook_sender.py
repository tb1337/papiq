"""The in-memory sender passes the contract suite, with a receiver that answers by path."""

import pytest

from papiq.adapters.outbound.memory import FakeWebhookSender
from papiq.core.ports.webhook_sender import WebhookRequest, WebhookSender
from tests.contracts.webhook_sender import Receiver, WebhookSenderContract
from tests.unit.adapters.webhooks.receiver import Received, default_answer

BASE = "http://receiver.test"
NOBODY = "http://nobody.test"


class _Receiver:
    """Records what the fake sender was asked to send."""

    def __init__(self, sender: FakeWebhookSender) -> None:
        self._sender = sender

    url = BASE
    unreachable_url = NOBODY

    def hits(self, path: str) -> int:
        return sum(1 for r in self._sender.requests if r.url == BASE + path)

    def last_body(self) -> bytes:
        return self._sender.requests[-1].body

    def last_headers(self) -> dict[str, str]:
        return {name.lower(): value for name, value in self._sender.requests[-1].headers.items()}


def _answer(request: WebhookRequest) -> int | str:
    if request.url.startswith(NOBODY):
        return "cannot connect"
    path = request.url.removeprefix(BASE)
    if path == "/slow":
        return f"no answer within {request.timeout.total_seconds():g} seconds"
    status, _ = default_answer(Received("POST", path, {}, request.body))
    return status


class TestFakeWebhookSender(WebhookSenderContract):
    @pytest.fixture
    def sender(self) -> WebhookSender:
        return FakeWebhookSender(_answer)

    @pytest.fixture
    def receiver(self, sender: WebhookSender) -> Receiver:
        assert isinstance(sender, FakeWebhookSender)
        return _Receiver(sender)
