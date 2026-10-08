"""From a document event to a signed request at a receiver: the worker with the real sender
and a receiver on the loopback interface. The receiver checks the signature the way the
README tells receivers to."""

import asyncio
import base64
import hashlib
import hmac
import json
import time
from dataclasses import replace
from datetime import timedelta

from papiq import __version__
from papiq.adapters.inbound.worker import Worker
from papiq.adapters.outbound.webhooks import HttpWebhookSender
from papiq.composition.container import build_memory_container, build_services
from papiq.composition.settings import Settings
from papiq.core.domain.webhooks import DeliveryOutcome
from papiq.core.services.webhooks import SUBSCRIBER
from tests import builders
from tests.builders import incoming
from tests.contracts.processing import SAMPLES
from tests.unit.adapters.webhooks.receiver import LocalReceiver, Received

TOLERANCE = 5 * 60  # seconds a request may be old


def verify(secret: str, received: Received) -> dict[str, object]:
    """The check for receivers (see the README): recompute the signature from the raw body,
    compare in constant time, refuse old timestamps."""
    message_id = received.headers["webhook-id"]
    timestamp = received.headers["webhook-timestamp"]
    assert abs(time.time() - int(timestamp)) <= TOLERANCE
    key = base64.b64decode(secret.removeprefix("whsec_"))
    expected = base64.b64encode(
        hmac.new(
            key, f"{message_id}.{timestamp}.".encode() + received.body, hashlib.sha256
        ).digest()
    ).decode()
    signatures = [part.split(",", 1)[1] for part in received.headers["webhook-signature"].split()]
    assert any(hmac.compare_digest(expected, signature) for signature in signatures)
    body: dict[str, object] = json.loads(received.body)
    assert body["id"] == message_id
    return body


async def test_a_green_document_reaches_the_receiver_signed_after_a_failed_try() -> None:
    answers = iter([503, 200])
    async with LocalReceiver(lambda _: (next(answers, 200), {})) as receiver:
        container = replace(
            build_memory_container(),
            webhook_sender=HttpWebhookSender(user_agent=f"Papiq/{__version__}"),
        )
        settings = Settings.model_construct(webhook_retry_delay=timedelta(milliseconds=50))
        services = build_services(container, settings)
        builders.skip_classification(services.pipeline)
        container.event_bus.subscribe(SUBSCRIBER, services.webhook_delivery.on_event)
        user = builders.user()
        async with container.unit_of_work() as uow:
            await uow.users.add(user)
            await uow.drawers.add(builders.default_drawer(user))
            await uow.commit()
        created = await services.webhooks.create(
            user.id, name="n8n", url=f"{receiver.url}/hook", event_types=["document.lane_changed"]
        )
        worker = Worker(
            pipeline=services.pipeline,
            maintenance=services.maintenance,
            event_bus=container.event_bus,
            concurrency=1,
            poll_interval=timedelta(milliseconds=10),
            dispatch_interval=timedelta(milliseconds=10),
            shutdown_timeout=timedelta(seconds=5),
            webhooks=services.webhook_delivery,
            webhook_concurrency=1,
        )
        task = asyncio.create_task(worker.run())
        try:
            document = await services.pipeline.receive(
                user.id, incoming((SAMPLES / "scan.pdf").read_bytes()), filename="a.pdf"
            )
            async with asyncio.timeout(10):
                while len(receiver.received) < 2:
                    await asyncio.sleep(0.01)
        finally:
            worker.stop()
            await asyncio.wait_for(task, timeout=10)

        first, second = receiver.received
        for request in (first, second):
            assert request.method == "POST" and request.path == "/hook"
            body = verify(created.secret, request)
            assert body["type"] == "document.lane_changed"
            assert body["document_id"] == str(document.id)
            assert set(body) == {"id", "type", "occurred_at", "document_id"}
        assert first.headers["webhook-id"] == second.headers["webhook-id"]  # the same event
        assert first.headers["content-type"] == "application/json"
        assert first.headers["user-agent"].startswith("Papiq/")

        deliveries = await services.webhooks.deliveries(user.id, created.webhook.id)
        assert [(d.attempt, d.outcome, d.status_code) for d in reversed(deliveries)] == [
            (1, DeliveryOutcome.RETRYING, 503),
            (2, DeliveryOutcome.DELIVERED, 200),
        ]
