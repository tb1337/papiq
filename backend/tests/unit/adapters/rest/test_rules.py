"""`/rules`: managing rules over the API."""

from papiq.adapters.inbound.rest import PREFIX
from tests.api import auth
from tests.unit.adapters.rest.conftest import Api
from tests.unit.adapters.rest.test_resources import post


async def test_a_rule_is_created_changed_disabled_and_deleted(api: Api) -> None:
    admin, owner, other = await api.admin(), await api.user(), await api.user()
    tag = await post(api, "/tags", {"name": "tax"}, auth(admin))
    body = {
        "name": "Tax",
        "conditions": {
            "all": [
                {"field": "text", "op": "contains", "value": "Steuer"},
                {"any": [{"field": "channel", "op": "is", "value": "web"}], "not": True},
            ]
        },
        "actions": [{"type": "add_tags", "tag_ids": [tag["id"]]}],
    }
    rule = await post(api, "/rules", body, auth(owner))
    assert rule["scope"] == "user" and rule["version"] == 1 and rule["enabled"]
    assert rule["conditions"]["all"][1]["not"] is True
    url = f"{PREFIX}/rules/{rule['id']}"

    assert (await api.client.get(url, headers=auth(other))).status_code == 404
    assert (await api.client.get(url, headers=auth(admin))).status_code == 200
    changed = await api.client.put(url, json={**body, "priority": 5}, headers=auth(owner))
    assert changed.status_code == 200, changed.text
    assert changed.json()["version"] == 2
    disabled = await api.client.patch(url, json={"enabled": False}, headers=auth(owner))
    assert disabled.json()["enabled"] is False
    versions = await api.client.get(f"{url}/versions", headers=auth(owner))
    assert [item["priority"] for item in versions.json()] == [100, 5]

    assert (await api.client.delete(url, headers=auth(owner))).status_code == 204
    assert (await api.client.get(url, headers=auth(owner))).status_code == 404
    assert (await api.client.get(f"{url}/versions/1", headers=auth(owner))).status_code == 200
    listed = await api.client.get(f"{PREFIX}/rules", headers=auth(owner))
    assert listed.json() == []


async def test_global_rules_are_for_admins_and_only_tag(api: Api) -> None:
    admin, user = await api.admin(), await api.user()
    contact = await post(api, "/contacts", {"name": "ACME"}, auth(admin))
    body = {
        "scope": "global",
        "name": "ACME",
        "conditions": {"all": [{"field": "contact", "op": "is", "value": contact["id"]}]},
        "actions": [{"type": "set_contact", "contact_id": contact["id"]}],
    }
    response = await api.client.post(f"{PREFIX}/rules", json=body, headers=auth(admin))
    assert response.status_code == 422, response.text
    body["actions"] = [{"type": "force_review", "reason": "check"}]
    response = await api.client.post(f"{PREFIX}/rules", json=body, headers=auth(user))
    assert response.status_code == 403
    rule = await post(api, "/rules", body, auth(admin))
    listed = await api.client.get(f"{PREFIX}/rules", headers=auth(user))
    assert [item["id"] for item in listed.json()] == [rule["id"]]


async def test_deleting_a_contact_disables_the_rules_that_use_it(api: Api) -> None:
    admin, owner = await api.admin(), await api.user()
    contact = await post(api, "/contacts", {"name": "ACME"}, auth(admin))
    body = {
        "name": "ACME",
        "conditions": {"all": [{"field": "channel", "op": "is", "value": "api"}]},
        "actions": [{"type": "set_contact", "contact_id": contact["id"]}],
    }
    rule = await post(api, "/rules", body, auth(owner))
    response = await api.client.delete(f"{PREFIX}/contacts/{contact['id']}", headers=auth(admin))
    assert response.status_code == 204
    current = (await api.client.get(f"{PREFIX}/rules/{rule['id']}", headers=auth(owner))).json()
    assert current["enabled"] is False
    assert current["disabled_reason"] == "contact 'ACME' was deleted"
    response = await api.client.patch(
        f"{PREFIX}/rules/{rule['id']}", json={"enabled": True}, headers=auth(owner)
    )
    assert response.status_code == 404
