"""Delivering events to webhooks: who is told, signing, answers, repetition, switching off."""

import base64
import hashlib
import hmac
import json
from datetime import timedelta

import pytest

from papiq.core.domain.documents import Document
from papiq.core.domain.drawers import Drawer, ShareLevel
from papiq.core.domain.errors import NotFoundError
from papiq.core.domain.events import (
    DocumentDeleted,
    DocumentFiled,
    DocumentReceived,
    LaneChanged,
)
from papiq.core.domain.ids import DocumentId, WebhookId, new_id
from papiq.core.domain.jobs import Job, JobStatus
from papiq.core.domain.pipeline import Lane
from papiq.core.domain.users import Role, User
from papiq.core.domain.webhooks import (
    DISABLED_FAILING,
    TEST_EVENT,
    DeliveryOutcome,
    Webhook,
    WebhookDelivery,
)
from papiq.core.ports import WebhookRequest
from papiq.core.services.webhooks import DELIVER_JOB, WebhookDeliveryService, WebhookPolicy
from tests import builders
from tests.builders import NOW
from tests.unit.services.conftest import World

URL = "http://n8n.lan:5678/webhook/papiq"


async def hook(world: World, owner: User, *types: str, url: str = URL) -> tuple[Webhook, str]:
    created = await world.webhooks.create(owner.id, name="n8n", url=url, event_types=types or ["*"])
    return created.webhook, created.secret


async def seed(
    world: World, owner: User, drawer: Drawer | None = None, *, green: bool = True
) -> Document:
    """A document of the owner; green, or still in processing."""
    drawer = drawer or await world.default_drawer(owner)
    document = builders.processed(owner, drawer) if green else builders.document(owner, drawer)
    async with world.uow() as uow:
        await uow.documents.add(document)
        await uow.commit()
    return document


def lane_changed(document: Document) -> LaneChanged:
    return LaneChanged(occurred_at=NOW, document_id=document.id, old=None, new=Lane.GREEN)


def deliveries(world: World) -> list[Job]:
    return [j for j in world.database.jobs.values() if j.kind == DELIVER_JOB]


async def log(world: World, webhook: Webhook) -> list[WebhookDelivery]:
    async with world.uow() as uow:
        rows = await uow.webhooks.deliveries(webhook.id)
    # Ids order only to the millisecond.
    return sorted(rows, key=lambda row: (row.started_at, row.attempt))


async def run_all(service: WebhookDeliveryService) -> int:
    count = 0
    while await service.run_next_job():
        count += 1
    return count


# --- fan-out ------------------------------------------------------------------------------------


async def test_an_event_is_queued_for_each_webhook_that_wants_its_type(world: World) -> None:
    user = await world.user()
    document = await seed(world, user)
    await hook(world, user, "document.lane_changed")
    await hook(world, user)  # all types
    await hook(world, user, "document.updated")
    off, _ = await hook(world, user)
    await world.webhooks.update(user.id, off.id, active=False)
    await world.webhook_delivery().on_event(lane_changed(document))
    assert len(deliveries(world)) == 2


async def test_only_those_who_may_read_the_document_are_told(world: World) -> None:
    owner, friend, stranger = await world.user(), await world.user(), await world.user()
    shared = builders.drawer(owner, "shared")
    shared.share(friend.id, ShareLevel.READ)
    async with world.uow() as uow:
        await uow.drawers.add(shared)
        await uow.commit()
    in_shared = await seed(world, owner, shared)
    private = await seed(world, owner)
    yellow = await seed(world, owner, shared, green=False)
    service = world.webhook_delivery()
    await hook(world, owner)
    await hook(world, friend)
    await hook(world, stranger)

    await service.on_event(lane_changed(in_shared))  # owner and friend
    assert len(deliveries(world)) == 2
    await service.on_event(lane_changed(private))  # owner only
    assert len(deliveries(world)) == 3
    await service.on_event(lane_changed(yellow))  # not green: owner only
    assert len(deliveries(world)) == 4


async def test_a_deactivated_owner_is_not_told(world: World) -> None:
    admin, user = await world.user(role=Role.ADMIN), await world.user()
    document = await seed(world, user)
    await hook(world, user)
    await world.users.set_active(admin.id, user.id, False)
    await world.webhook_delivery().on_event(lane_changed(document))
    assert deliveries(world) == []


async def test_filed_is_skipped_while_the_document_has_no_lane(world: World) -> None:
    user = await world.user()
    await hook(world, user, "document.filed")
    service = world.webhook_delivery()
    drawer = await world.default_drawer(user)
    early = await seed(world, user, green=False)
    await service.on_event(
        DocumentFiled(occurred_at=NOW, document_id=early.id, drawer_id=drawer.id)
    )
    assert deliveries(world) == []
    done = await seed(world, user)
    await service.on_event(DocumentFiled(occurred_at=NOW, document_id=done.id, drawer_id=drawer.id))
    assert len(deliveries(world)) == 1


async def test_a_deleted_document_is_reported_to_the_readers_named_in_the_event(
    world: World,
) -> None:
    owner, friend, stranger = await world.user(), await world.user(), await world.user()
    for user in (owner, friend, stranger):
        await hook(world, user, "document.deleted")
    gone = DocumentId(new_id())
    await world.webhook_delivery().on_event(
        DocumentDeleted(
            occurred_at=NOW,
            document_id=gone,
            readers=(owner.id, friend.id),
        )
    )
    assert len(deliveries(world)) == 2


async def test_an_event_delivered_twice_queues_one_job(world: World) -> None:
    user = await world.user()
    document = await seed(world, user)
    await hook(world, user)
    service = world.webhook_delivery()
    event = lane_changed(document)
    await service.on_event(event)
    await service.on_event(event)
    assert len(deliveries(world)) == 1


async def test_events_without_a_document_or_for_a_vanished_one_queue_nothing(
    world: World,
) -> None:
    user = await world.user()
    await hook(world, user)
    await world.webhook_delivery().on_event(
        DocumentReceived(occurred_at=NOW, document_id=DocumentId(new_id()))
    )
    assert deliveries(world) == []


# --- delivery -----------------------------------------------------------------------------------


def expected_signature(secret: str, request: WebhookRequest) -> str:
    key = base64.b64decode(secret.removeprefix("whsec_"))
    message = request.headers["webhook-id"], request.headers["webhook-timestamp"]
    content = f"{message[0]}.{message[1]}.".encode() + request.body
    return "v1," + base64.b64encode(hmac.new(key, content, hashlib.sha256).digest()).decode()


async def queue(world: World, document: Document, service: WebhookDeliveryService) -> LaneChanged:
    event = lane_changed(document)
    await service.on_event(event)
    return event


async def test_a_delivery_is_signed_sent_and_logged(world: World) -> None:
    user = await world.user()
    document = await seed(world, user)
    webhook, secret = await hook(world, user)
    service = world.webhook_delivery()
    event = await queue(world, document, service)

    assert await service.run_next_job()
    (request,) = world.sender.requests
    assert request.url == URL
    assert request.headers["Content-Type"] == "application/json"
    assert request.headers["webhook-id"] == str(event.id)
    assert request.headers["webhook-signature"] == expected_signature(secret, request)
    assert json.loads(request.body) == {
        "id": str(event.id),
        "type": "document.lane_changed",
        "occurred_at": "2026-10-06T12:00:00Z",
        "document_id": str(document.id),
    }
    (row,) = await log(world, webhook)
    assert (row.outcome, row.status_code, row.attempt) == (DeliveryOutcome.DELIVERED, 200, 1)
    assert row.event_id == event.id and row.document_id == document.id
    assert [j.status for j in deliveries(world)] == [JobStatus.DONE]
    assert not await service.run_next_job()


async def test_a_renewed_secret_signs_next_to_the_new_one_during_the_grace_period(
    world: World,
) -> None:
    user = await world.user()
    document = await seed(world, user)
    webhook, old = await hook(world, user)
    new = (await world.webhooks.renew_secret(user.id, webhook.id)).secret
    service = world.webhook_delivery()
    await queue(world, document, service)
    await service.run_next_job()
    request = world.sender.requests[0]
    assert request.headers["webhook-signature"] == " ".join(
        expected_signature(secret, request) for secret in (new, old)
    )
    # After the grace period only the new one signs.
    world.clock.advance(timedelta(hours=25))
    await queue(world, document, service)
    await service.run_next_job()
    request = world.sender.requests[1]
    assert request.headers["webhook-signature"] == expected_signature(new, request)


@pytest.mark.parametrize("status", [500, 503, 408, 425, 429, "unreachable"])
async def test_temporary_failures_are_repeated_with_a_growing_delay(
    world: World, status: int | str
) -> None:
    world.sender.answers = [status, status, 204]
    user = await world.user()
    document = await seed(world, user)
    webhook, _ = await hook(world, user)
    service = world.webhook_delivery()
    await queue(world, document, service)

    await service.run_next_job()
    assert not await service.run_next_job()  # waits for the retry
    world.clock.advance(timedelta(seconds=30))
    await service.run_next_job()
    world.clock.advance(timedelta(seconds=59))
    assert not await service.run_next_job()
    world.clock.advance(timedelta(seconds=1))  # 60 s after the second failure
    await service.run_next_job()

    rows = await log(world, webhook)
    assert [(r.attempt, r.outcome) for r in rows] == [
        (1, DeliveryOutcome.RETRYING),
        (2, DeliveryOutcome.RETRYING),
        (3, DeliveryOutcome.DELIVERED),
    ]
    assert rows[0].next_attempt_at == NOW + timedelta(seconds=30)
    assert rows[1].next_attempt_at == NOW + timedelta(seconds=30 + 60)
    assert rows[0].status_code == (None if status == "unreachable" else status)
    assert (rows[0].error is not None) == (status == "unreachable")


async def test_it_gives_up_after_the_last_attempt(world: World) -> None:
    world.sender.answers = [500]
    user = await world.user()
    document = await seed(world, user)
    webhook, _ = await hook(world, user)
    service = world.webhook_delivery(WebhookPolicy(max_attempts=3))
    await queue(world, document, service)
    for _ in range(3):
        await service.run_next_job()
        world.clock.advance(timedelta(hours=2))
    assert not await service.run_next_job()
    assert [r.outcome for r in await log(world, webhook)] == [
        DeliveryOutcome.RETRYING,
        DeliveryOutcome.RETRYING,
        DeliveryOutcome.GAVE_UP,
    ]
    assert len(world.sender.requests) == 3
    assert (await world.webhooks.get(user.id, webhook.id)).failed_streak == 1


@pytest.mark.parametrize("status", [301, 302, 400, 401, 404, 410, 422])
async def test_redirects_and_client_errors_are_final(world: World, status: int) -> None:
    world.sender.answers = [status]
    user = await world.user()
    document = await seed(world, user)
    webhook, _ = await hook(world, user)
    service = world.webhook_delivery()
    await queue(world, document, service)
    await service.run_next_job()
    world.clock.advance(timedelta(days=1))
    assert not await service.run_next_job()
    (row,) = await log(world, webhook)
    assert (row.outcome, row.status_code) == (DeliveryOutcome.GAVE_UP, status)
    assert len(world.sender.requests) == 1


async def test_a_webhook_is_switched_off_after_repeated_failures_and_success_resets(
    world: World,
) -> None:
    user = await world.user()
    document = await seed(world, user)
    webhook, _ = await hook(world, user)
    service = world.webhook_delivery(WebhookPolicy(disable_after=3))

    async def deliver(answer: int) -> None:
        world.sender.answers = [answer]
        await service.on_event(lane_changed(document))
        await service.run_next_job()

    await deliver(404)
    await deliver(404)
    await deliver(200)  # resets the count
    assert (await world.webhooks.get(user.id, webhook.id)).failed_streak == 0
    await deliver(404)
    await deliver(404)
    assert (await world.webhooks.get(user.id, webhook.id)).active
    await deliver(404)
    off = await world.webhooks.get(user.id, webhook.id)
    assert (off.active, off.disabled_reason, off.failed_streak) == (False, DISABLED_FAILING, 3)
    # Switched off: nothing is queued any more.
    before = len(deliveries(world))
    await service.on_event(lane_changed(document))
    assert len(deliveries(world)) == before


# --- rights at the time of the attempt ----------------------------------------------------------


async def test_a_lost_right_drops_the_delivery_without_sending(world: World) -> None:
    owner, friend = await world.user(), await world.user()
    shared = builders.drawer(owner, "shared")
    shared.share(friend.id, ShareLevel.READ)
    async with world.uow() as uow:
        await uow.drawers.add(shared)
        await uow.commit()
    document = await seed(world, owner, shared)
    webhook, _ = await hook(world, friend)
    service = world.webhook_delivery()
    await queue(world, document, service)
    await world.drawers.unshare(owner.id, shared.id, friend.id)

    assert await service.run_next_job()
    assert world.sender.requests == []
    (row,) = await log(world, webhook)
    assert (row.outcome, row.status_code) == (DeliveryOutcome.DROPPED, None)
    assert row.error == "the document is not available to the owner"
    assert (await world.webhooks.get(friend.id, webhook.id)).failed_streak == 0
    assert not await service.run_next_job()


async def test_a_switched_off_webhook_or_deactivated_owner_or_vanished_document_is_dropped(
    world: World,
) -> None:
    admin = await world.user(role=Role.ADMIN)
    a, b, c = await world.user(), await world.user(), await world.user()
    service = world.webhook_delivery()
    docs, hooks = [], []
    for owner in (a, b, c):
        document = await seed(world, owner)
        docs.append(document)
        hooks.append((await hook(world, owner))[0])
        await queue(world, document, service)
    await world.webhooks.update(a.id, hooks[0].id, active=False)
    await world.users.set_active(admin.id, b.id, False)
    async with world.uow() as uow:
        await uow.documents.remove(docs[2].id)
        await uow.commit()

    assert await run_all(service) == 3
    assert world.sender.requests == []
    reasons = [(await log(world, h))[0].error for h in hooks]
    assert reasons == [
        "the webhook is switched off",
        "the owner's account is not active",
        "the document is not available to the owner",
    ]


async def test_a_deleted_webhook_ends_its_queued_deliveries(world: World) -> None:
    user = await world.user()
    document = await seed(world, user)
    webhook, _ = await hook(world, user)
    service = world.webhook_delivery()
    await queue(world, document, service)
    await world.webhooks.delete(user.id, webhook.id)
    assert await service.run_next_job()
    assert world.sender.requests == []
    assert [j.status for j in deliveries(world)] == [JobStatus.DONE]


async def test_a_deleted_document_is_delivered_without_checking_the_rights_again(
    world: World,
) -> None:
    user = await world.user()
    webhook, _ = await hook(world, user, "document.deleted")
    service = world.webhook_delivery()
    document_id = DocumentId(new_id())
    await service.on_event(
        DocumentDeleted(occurred_at=NOW, document_id=document_id, readers=(user.id,))
    )
    await service.run_next_job()
    (row,) = await log(world, webhook)
    assert row.outcome is DeliveryOutcome.DELIVERED and row.document_id == document_id


async def test_an_unusable_secret_gives_up(world: World) -> None:
    user = await world.user()
    document = await seed(world, user)
    webhook, _ = await hook(world, user)
    async with world.uow() as uow:
        stored = await uow.webhooks.get(webhook.id)
        stored.encrypted_secret = b"garbage" * 8
        await uow.webhooks.update(stored)
        await uow.commit()
    service = world.webhook_delivery()
    await queue(world, document, service)
    await service.run_next_job()
    (row,) = await log(world, webhook)
    assert row.outcome is DeliveryOutcome.GAVE_UP and "secret" in (row.error or "")
    assert world.sender.requests == []


# --- test request -------------------------------------------------------------------------------


async def test_the_test_request_is_sent_once_and_logged(world: World) -> None:
    user = await world.user()
    webhook, secret = await hook(world, user, "document.filed")
    service = world.webhook_delivery()
    row = await service.send_test(user.id, webhook.id)
    (request,) = world.sender.requests
    assert json.loads(request.body)["type"] == TEST_EVENT
    assert json.loads(request.body)["document_id"] is None
    assert request.headers["webhook-signature"] == expected_signature(secret, request)
    assert (row.outcome, row.event_type, row.document_id) == (
        DeliveryOutcome.DELIVERED,
        TEST_EVENT,
        None,
    )
    assert await log(world, webhook) == [row]

    world.sender.answers = [500]
    failed = await service.send_test(user.id, webhook.id)
    assert (failed.outcome, failed.status_code, failed.next_attempt_at) == (
        DeliveryOutcome.GAVE_UP,
        500,
        None,
    )
    assert len(world.sender.requests) == 2  # no repetition
    assert (await world.webhooks.get(user.id, webhook.id)).failed_streak == 0


async def test_the_owner_and_admins_test_a_webhook(world: World) -> None:
    admin, owner, other = await world.user(role=Role.ADMIN), await world.user(), await world.user()
    webhook, _ = await hook(world, owner)
    service = world.webhook_delivery()
    with pytest.raises(NotFoundError):
        await service.send_test(other.id, webhook.id)
    with pytest.raises(NotFoundError):
        await service.send_test(owner.id, WebhookId(new_id()))
    assert world.sender.requests == []
    await service.send_test(admin.id, webhook.id)
    assert len(world.sender.requests) == 1
