"""`/webhooks`: managing webhooks over the API."""

from uuid import UUID

from papiq.adapters.inbound.rest import PREFIX
from papiq.adapters.outbound.memory import FakeWebhookSender
from papiq.core.domain.identity import TokenScope
from papiq.core.domain.ids import DocumentId, EventId, WebhookId, new_id
from papiq.core.domain.webhooks import DeliveryOutcome, WebhookDelivery
from tests.api import auth, bearer
from tests.unit.adapters.rest.conftest import Api
from tests.unit.adapters.rest.test_resources import post

BODY = {
    "name": "n8n",
    "url": "http://n8n.lan:5678/webhook/papiq",
    "event_types": ["document.filed", "document.lane_changed"],
}


async def test_a_webhook_is_created_changed_switched_off_and_deleted(api: Api) -> None:
    user = await api.user()
    created = await post(api, "/webhooks", BODY, auth(user))
    assert created["secret"].startswith("whsec_")
    assert created["active"] is True and created["event_types"] == [
        "document.filed",
        "document.lane_changed",
    ]
    url = f"{PREFIX}/webhooks/{created['id']}"

    shown = (await api.client.get(url, headers=auth(user))).json()
    assert "secret" not in shown and shown["id"] == created["id"]
    listed = (await api.client.get(f"{PREFIX}/webhooks", headers=auth(user))).json()
    assert [item["id"] for item in listed] == [created["id"]]
    assert all("secret" not in item for item in listed)

    changed = await api.client.patch(
        url, json={"name": "HA", "event_types": ["*"], "active": False}, headers=auth(user)
    )
    assert changed.status_code == 200, changed.text
    assert (changed.json()["name"], changed.json()["event_types"], changed.json()["active"]) == (
        "HA",
        ["*"],
        False,
    )
    assert (await api.client.delete(url, headers=auth(user))).status_code == 204
    assert (await api.client.get(url, headers=auth(user))).status_code == 404


async def test_the_secret_is_renewed_once_with_the_old_one_still_valid(api: Api) -> None:
    user = await api.user()
    created = await post(api, "/webhooks", BODY, auth(user))
    response = await api.client.post(
        f"{PREFIX}/webhooks/{created['id']}/secret", headers=auth(user)
    )
    assert response.status_code == 200, response.text
    renewed = response.json()
    assert renewed["secret"] != created["secret"]
    assert renewed["previous_secret_valid_until"] is not None
    shown = (await api.client.get(f"{PREFIX}/webhooks/{created['id']}", headers=auth(user))).json()
    assert "secret" not in shown and shown["previous_secret_valid_until"] is not None


async def test_invalid_input_is_a_422(api: Api) -> None:
    user = await api.user()
    for body in (
        {**BODY, "url": "ftp://x"},
        {**BODY, "url": "https://user:pw@example.org"},
        {**BODY, "event_types": []},
        {**BODY, "event_types": ["document.nonsense"]},
        {**BODY, "name": ""},
        {**BODY, "extra": 1},
    ):
        response = await api.client.post(f"{PREFIX}/webhooks", json=body, headers=auth(user))
        assert response.status_code == 422, body
    url = f"{PREFIX}/webhooks"
    created = await post(api, "/webhooks", BODY, auth(user))
    response = await api.client.patch(
        f"{url}/{created['id']}", json={"url": "nonsense"}, headers=auth(user)
    )
    assert response.status_code == 422


async def test_users_see_only_their_webhooks_and_admins_all(api: Api) -> None:
    owner, other, admin = await api.user(), await api.user(), await api.admin()
    mine = await post(api, "/webhooks", BODY, auth(owner))
    url = f"{PREFIX}/webhooks/{mine['id']}"
    assert (await api.client.get(url, headers=auth(other))).status_code == 404
    assert (await api.client.get(f"{url}/deliveries", headers=auth(other))).status_code == 404
    assert (await api.client.patch(url, json={"name": "x"}, headers=auth(other))).status_code == 404
    assert (await api.client.get(f"{PREFIX}/webhooks", headers=auth(other))).json() == []

    assert (await api.client.get(url, headers=auth(admin))).status_code == 200
    assert (await api.client.get(f"{url}/deliveries", headers=auth(admin))).json() == []
    everything = await api.client.get(f"{PREFIX}/webhooks", headers=auth(admin))
    assert [item["id"] for item in everything.json()] == [mine["id"]]
    narrowed = await api.client.get(
        f"{PREFIX}/webhooks", params={"owner": str(new_id())}, headers=auth(admin)
    )
    assert narrowed.json() == []
    # Admins change, renew and delete as well.
    changed = await api.client.patch(url, json={"name": "x"}, headers=auth(admin))
    assert (changed.status_code, changed.json()["name"]) == (200, "x")
    renewed = await api.client.post(f"{url}/secret", headers=auth(admin))
    assert renewed.status_code == 200 and renewed.json()["secret"].startswith("whsec_")
    assert (await api.client.delete(url, headers=auth(admin))).status_code == 204
    assert (await api.client.get(url, headers=auth(owner))).status_code == 404


async def test_a_read_token_reads_but_changes_nothing(api: Api) -> None:
    user = await api.user()
    created = await post(api, "/webhooks", BODY, auth(user))
    _, token = await api.services.auth.create_api_token(user.id, "reader", TokenScope.READ)
    headers = bearer(token)
    url = f"{PREFIX}/webhooks"
    assert (await api.client.get(url, headers=headers)).status_code == 200
    assert (await api.client.get(f"{url}/{created['id']}", headers=headers)).status_code == 200
    assert (
        await api.client.get(f"{url}/{created['id']}/deliveries", headers=headers)
    ).status_code == 200
    assert (await api.client.post(url, json=BODY, headers=headers)).status_code == 403
    assert (
        await api.client.patch(f"{url}/{created['id']}", json={}, headers=headers)
    ).status_code == 403
    assert (await api.client.delete(f"{url}/{created['id']}", headers=headers)).status_code == 403
    assert (
        await api.client.post(f"{url}/{created['id']}/secret", headers=headers)
    ).status_code == 403


async def test_webhooks_need_authentication_and_the_limit_is_a_409(api: Api) -> None:
    assert (await api.client.get(f"{PREFIX}/webhooks")).status_code == 401
    user = await api.user()
    for _ in range(20):
        await post(api, "/webhooks", BODY, auth(user))
    response = await api.client.post(f"{PREFIX}/webhooks", json=BODY, headers=auth(user))
    assert response.status_code == 409


async def test_the_deliveries_are_listed_newest_first_in_pages(api: Api) -> None:
    user = await api.user()
    created = await post(api, "/webhooks", BODY, auth(user))
    ids = []
    async with api.container.unit_of_work() as uow:
        for attempt in range(1, 4):
            delivery = WebhookDelivery.record(
                webhook_id=WebhookId(UUID(created["id"])),
                event_id=EventId(new_id()),
                event_type="document.filed",
                document_id=DocumentId(new_id()),
                attempt=attempt,
                started_at=api.clock.now(),
                duration_ms=7,
                outcome=DeliveryOutcome.DELIVERED,
                status_code=204,
            )
            ids.append(str(delivery.id))
            await uow.webhooks.add_delivery(delivery)
        await uow.commit()
    url = f"{PREFIX}/webhooks/{created['id']}/deliveries"
    first = (await api.client.get(url, params={"limit": 2}, headers=auth(user))).json()
    assert [item["id"] for item in first] == sorted(ids, reverse=True)[:2]
    assert first[0]["outcome"] == "delivered" and first[0]["status_code"] == 204
    rest = await api.client.get(url, params={"before": first[-1]["id"]}, headers=auth(user))
    assert [item["id"] for item in rest.json()] == sorted(ids, reverse=True)[2:]


async def test_a_test_request_is_sent_and_logged(api: Api) -> None:
    user, other, admin = await api.user(), await api.user(), await api.admin()
    created = await post(api, "/webhooks", BODY, auth(user))
    url = f"{PREFIX}/webhooks/{created['id']}/test"
    response = await api.client.post(url, headers=auth(user))
    assert response.status_code == 200, response.text
    row = response.json()
    assert (row["outcome"], row["event_type"], row["document_id"]) == (
        "delivered",
        "webhook.test",
        None,
    )
    sender = api.container.webhook_sender
    assert isinstance(sender, FakeWebhookSender) and len(sender.requests) == 1
    listed = await api.client.get(
        f"{PREFIX}/webhooks/{created['id']}/deliveries", headers=auth(user)
    )
    assert [item["id"] for item in listed.json()] == [row["id"]]

    assert (await api.client.post(url, headers=auth(other))).status_code == 404
    _, token = await api.services.auth.create_api_token(user.id, "reader", TokenScope.READ)
    assert (await api.client.post(url, headers=bearer(token))).status_code == 403
    assert (await api.client.post(url)).status_code == 401
    assert len(sender.requests) == 1
    assert (await api.client.post(url, headers=auth(admin))).status_code == 200
    assert len(sender.requests) == 2
