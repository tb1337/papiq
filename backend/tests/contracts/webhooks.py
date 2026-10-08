"""Contract suite for `WebhookRepository` in the unit of work: webhooks and the log of their
deliveries."""

from datetime import timedelta

import pytest

from papiq.core.domain.errors import ConcurrencyError, NotFoundError
from papiq.core.domain.ids import DocumentId, EventId, new_id
from papiq.core.domain.users import User
from papiq.core.domain.webhooks import (
    ALL_EVENTS,
    DeliveryOutcome,
    Webhook,
    WebhookDelivery,
)
from papiq.core.ports import UnitOfWorkFactory
from tests import builders
from tests.builders import NOW
from tests.contracts.unit_of_work import seed

HOUR = timedelta(hours=1)


def a_webhook(
    owner: User,
    *,
    types: tuple[str, ...] = (ALL_EVENTS,),
    at: timedelta = timedelta(0),
    **fields: object,
) -> Webhook:
    values: dict[str, object] = {
        "owner_id": owner.id,
        "name": "n8n",
        "url": "http://n8n.lan:5678/webhook/papiq",
        "event_types": types,
        "encrypted_secret": b"\x01encrypted",
        "now": NOW + at,
    }
    return Webhook.create(**{**values, **fields})  # type: ignore[arg-type]


def a_delivery(
    webhook: Webhook,
    *,
    at: timedelta = timedelta(0),
    outcome: DeliveryOutcome = DeliveryOutcome.DELIVERED,
) -> WebhookDelivery:
    return WebhookDelivery.record(
        webhook_id=webhook.id,
        event_id=EventId(new_id()),
        event_type="document.filed",
        document_id=DocumentId(new_id()),
        attempt=1,
        started_at=NOW + at,
        duration_ms=12,
        outcome=outcome,
        status_code=200,
    )


async def a_user(uow_factory: UnitOfWorkFactory) -> User:
    user = builders.user()
    await seed(uow_factory, user)
    return user


class WebhookRepositoryContract:
    async def test_a_webhook_round_trips_with_all_its_fields(
        self, uow_factory: UnitOfWorkFactory
    ) -> None:
        owner = await a_user(uow_factory)
        webhook = a_webhook(owner, types=("document.filed", "document.deleted"))
        webhook.renew_secret(b"\x01second", now=NOW + HOUR, grace=timedelta(hours=24))
        webhook.failed_streak = 4
        async with uow_factory() as uow:
            await uow.webhooks.add(webhook)
            await uow.commit()
        async with uow_factory() as uow:
            assert await uow.webhooks.get(webhook.id) == webhook
            assert await uow.webhooks.find(webhook.id) == webhook

    async def test_a_missing_webhook_is_not_found(self, uow_factory: UnitOfWorkFactory) -> None:
        async with uow_factory() as uow:
            assert await uow.webhooks.find(a_webhook(builders.user()).id) is None
            with pytest.raises(NotFoundError):
                await uow.webhooks.get(a_webhook(builders.user()).id)

    async def test_update_checks_the_version(self, uow_factory: UnitOfWorkFactory) -> None:
        owner = await a_user(uow_factory)
        webhook = a_webhook(owner)
        async with uow_factory() as uow:
            await uow.webhooks.add(webhook)
            await uow.commit()
        async with uow_factory() as first, uow_factory() as second:
            one, other = await first.webhooks.get(webhook.id), await second.webhooks.get(webhook.id)
            one.change(now=NOW + HOUR, name="first")
            await first.webhooks.update(one)
            await first.commit()
            other.change(now=NOW + HOUR, name="second")
            with pytest.raises(ConcurrencyError):
                await second.webhooks.update(other)
                await second.commit()
        async with uow_factory() as uow:
            stored = await uow.webhooks.get(webhook.id)
            assert (stored.name, stored.version) == ("first", 2)
        assert one.version == 2

    async def test_reads_return_copies(self, uow_factory: UnitOfWorkFactory) -> None:
        owner = await a_user(uow_factory)
        webhook = a_webhook(owner)
        async with uow_factory() as uow:
            await uow.webhooks.add(webhook)
            await uow.commit()
        async with uow_factory() as uow:
            (await uow.webhooks.get(webhook.id)).change(now=NOW, name="changed")
            assert (await uow.webhooks.get(webhook.id)).name == "n8n"

    async def test_lists_are_oldest_first_and_per_owner(
        self, uow_factory: UnitOfWorkFactory
    ) -> None:
        owner, other = await a_user(uow_factory), await a_user(uow_factory)
        second = a_webhook(owner, at=HOUR, name="second")
        first = a_webhook(owner, name="first")
        foreign = a_webhook(other, at=2 * HOUR, name="foreign")
        async with uow_factory() as uow:
            for webhook in (second, first, foreign):
                await uow.webhooks.add(webhook)
            await uow.commit()
        async with uow_factory() as uow:
            assert [w.name for w in await uow.webhooks.list_for_owner(owner.id)] == [
                "first",
                "second",
            ]
            assert [w.name for w in await uow.webhooks.list_all()] == ["first", "second", "foreign"]
            assert await uow.webhooks.count_for_owner(owner.id) == 2
            assert await uow.webhooks.count_for_owner(other.id) == 1

    async def test_active_for_an_event_type_lists_those_that_want_it(
        self, uow_factory: UnitOfWorkFactory
    ) -> None:
        owner = await a_user(uow_factory)
        everything = a_webhook(owner, name="all")
        filed = a_webhook(owner, types=("document.filed",), name="filed", at=HOUR)
        updated = a_webhook(owner, types=("document.updated",), name="updated", at=2 * HOUR)
        off = a_webhook(owner, name="off", at=3 * HOUR, active=False)
        async with uow_factory() as uow:
            for webhook in (everything, filed, updated, off):
                await uow.webhooks.add(webhook)
            await uow.commit()
        async with uow_factory() as uow:
            names = [w.name for w in await uow.webhooks.list_active_for("document.filed")]
            assert names == ["all", "filed"]
            assert [w.name for w in await uow.webhooks.list_active_for("webhook.other")] == ["all"]

    async def test_remove_takes_the_deliveries_along_and_missing_is_a_no_op(
        self, uow_factory: UnitOfWorkFactory
    ) -> None:
        owner = await a_user(uow_factory)
        webhook, keep = a_webhook(owner), a_webhook(owner, name="keep")
        async with uow_factory() as uow:
            await uow.webhooks.add(webhook)
            await uow.webhooks.add(keep)
            await uow.webhooks.add_delivery(a_delivery(webhook))
            await uow.webhooks.add_delivery(a_delivery(keep))
            await uow.commit()
        async with uow_factory() as uow:
            await uow.webhooks.remove(webhook.id)
            await uow.webhooks.remove(webhook.id)
            await uow.commit()
        async with uow_factory() as uow:
            assert await uow.webhooks.find(webhook.id) is None
            assert await uow.webhooks.deliveries(webhook.id) == []
            assert len(await uow.webhooks.deliveries(keep.id)) == 1

    async def test_remove_for_owner_removes_their_webhooks_only(
        self, uow_factory: UnitOfWorkFactory
    ) -> None:
        owner, other = await a_user(uow_factory), await a_user(uow_factory)
        mine, theirs = a_webhook(owner), a_webhook(other)
        async with uow_factory() as uow:
            await uow.webhooks.add(mine)
            await uow.webhooks.add(theirs)
            await uow.webhooks.add_delivery(a_delivery(mine))
            await uow.commit()
        async with uow_factory() as uow:
            assert await uow.webhooks.remove_for_owner(owner.id) == 1
            assert await uow.webhooks.remove_for_owner(owner.id) == 0
            await uow.commit()
        async with uow_factory() as uow:
            assert [w.id for w in await uow.webhooks.list_all()] == [theirs.id]

    async def test_deliveries_round_trip_newest_first_in_pages(
        self, uow_factory: UnitOfWorkFactory
    ) -> None:
        owner = await a_user(uow_factory)
        webhook = a_webhook(owner)
        deliveries = [a_delivery(webhook, at=timedelta(minutes=n)) for n in range(5)]
        deliveries[3].outcome = DeliveryOutcome.RETRYING
        deliveries[3].status_code = 503
        deliveries[3].error = "HTTP 503"
        deliveries[3].next_attempt_at = NOW + HOUR
        async with uow_factory() as uow:
            await uow.webhooks.add(webhook)
            for delivery in deliveries:
                await uow.webhooks.add_delivery(delivery)
            await uow.commit()
        newest_first = sorted(deliveries, key=lambda delivery: delivery.id, reverse=True)
        async with uow_factory() as uow:
            page = await uow.webhooks.deliveries(webhook.id, limit=2)
            assert page == newest_first[:2]
            after = await uow.webhooks.deliveries(webhook.id, before=page[-1].id, limit=10)
            assert after == newest_first[2:]
            assert await uow.webhooks.deliveries(webhook.id) == newest_first

    async def test_a_delivery_needs_its_webhook(self, uow_factory: UnitOfWorkFactory) -> None:
        owner = await a_user(uow_factory)
        gone = a_webhook(owner)
        async with uow_factory() as uow:
            with pytest.raises(NotFoundError):
                await uow.webhooks.add_delivery(a_delivery(gone))

    async def test_purge_removes_old_deliveries(self, uow_factory: UnitOfWorkFactory) -> None:
        owner = await a_user(uow_factory)
        webhook = a_webhook(owner)
        old, edge, recent = (
            a_delivery(webhook, at=timedelta(days=-3)),
            a_delivery(webhook, at=timedelta(days=-2)),
            a_delivery(webhook, at=timedelta(days=-1)),
        )
        async with uow_factory() as uow:
            await uow.webhooks.add(webhook)
            for delivery in (old, edge, recent):
                await uow.webhooks.add_delivery(delivery)
            await uow.commit()
        async with uow_factory() as uow:
            assert await uow.webhooks.purge_deliveries(before=NOW - timedelta(days=2)) == 1
            await uow.commit()
        async with uow_factory() as uow:
            kept = {delivery.id for delivery in await uow.webhooks.deliveries(webhook.id)}
            assert kept == {edge.id, recent.id}

    async def test_a_rolled_back_unit_leaves_nothing(self, uow_factory: UnitOfWorkFactory) -> None:
        owner = await a_user(uow_factory)
        webhook = a_webhook(owner)
        async with uow_factory() as uow:
            await uow.webhooks.add(webhook)
            await uow.webhooks.add_delivery(a_delivery(webhook))
            await uow.rollback()
        async with uow_factory() as uow:
            assert await uow.webhooks.find(webhook.id) is None
