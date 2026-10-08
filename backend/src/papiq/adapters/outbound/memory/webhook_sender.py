"""A stand-in webhook receiver for tests: the test decides what each request is answered with."""

from collections.abc import Callable

from papiq.core.ports.webhook_sender import WebhookRequest, WebhookResponse

type Answer = int | str | Exception
"""A status code, or an error text (no answer), or an exception that is turned into an error."""


class FakeWebhookSender:
    """Records the requests. Answers with 200 unless `answers` is given: a list used in turn
    (the last one stays), or a function of the request."""

    def __init__(
        self, answers: list[Answer] | Callable[[WebhookRequest], Answer] | None = None
    ) -> None:
        self.requests: list[WebhookRequest] = []
        self.answers: list[Answer] | Callable[[WebhookRequest], Answer] = (
            [200] if answers is None else answers
        )

    async def send(self, request: WebhookRequest) -> WebhookResponse:
        self.requests.append(request)
        if callable(self.answers):
            answer = self.answers(request)
        else:
            answer = self.answers[min(len(self.requests), len(self.answers)) - 1]
        if isinstance(answer, int):
            return WebhookResponse(status_code=answer, duration_ms=5)
        text = type(answer).__name__ if isinstance(answer, Exception) else answer
        return WebhookResponse(status_code=None, duration_ms=5, error=text)
