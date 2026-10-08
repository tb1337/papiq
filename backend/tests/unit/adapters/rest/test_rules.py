"""`/rules`: managing rules over the API."""

from papiq.adapters.inbound.rest import PREFIX
from tests.api import auth
from tests.contracts.processing import SAMPLES
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


async def upload(api: Api, headers: dict[str, str]) -> str:
    files = {"file": ("Bill 2026.pdf", (SAMPLES / "scan.pdf").read_bytes(), "x/y")}
    response = await api.client.post(f"{PREFIX}/documents", files=files, headers=headers)
    assert response.status_code == 202, response.text
    await api.drain()
    return str(response.json()["id"])


async def test_rules_act_on_arrival_and_conflicts_wait_in_the_inbox(api: Api) -> None:
    admin, owner = await api.admin(), await api.user()
    a, o = auth(admin), auth(owner)
    tax = await post(api, "/tags", {"name": "tax"}, a)
    drawer = await post(api, "/drawers", {"name": "Household"}, o)
    other = await post(api, "/drawers", {"name": "Office"}, o)
    api_channel = {"all": [{"field": "channel", "op": "is", "value": "api"}]}
    await post(
        api,
        "/rules",
        {
            "name": "Tag",
            "conditions": api_channel,
            "actions": [
                {"type": "add_tags", "tag_ids": [tax["id"]]},
                {"type": "set_title", "template": "Scan {filename}"},
                {"type": "set_drawer", "drawer_id": drawer["id"]},
            ],
        },
        o,
    )
    id = await upload(api, o)
    document = (await api.client.get(f"{PREFIX}/documents/{id}", headers=o)).json()
    assert document["lane"] == "green", document
    assert document["tag_ids"] == [tax["id"]]
    assert document["title"] == "Scan Bill 2026"
    assert document["drawer_id"] == drawer["id"]
    assert document["channel"] == "api"

    await post(
        api,
        "/rules",
        {
            "name": "Office",
            "conditions": api_channel,
            "actions": [{"type": "set_drawer", "drawer_id": other["id"]}],
        },
        o,
    )
    id = await upload_other(api, o)
    document = (await api.client.get(f"{PREFIX}/documents/{id}", headers=o)).json()
    assert document["lane"] == "yellow", document
    review = (await api.client.get(f"{PREFIX}/documents/{id}/review", headers=o)).json()
    fields = [field["field"] for step in review["open"] for field in step["fields"]]
    assert fields == ["drawer"]
    response = await api.client.post(
        f"{PREFIX}/documents/{id}/confirm", json={"drawer_id": other["id"]}, headers=o
    )
    assert response.status_code == 202, response.text
    await api.drain()
    document = (await api.client.get(f"{PREFIX}/documents/{id}", headers=o)).json()
    assert document["lane"] == "green", document
    assert document["drawer_id"] == other["id"]


async def upload_other(api: Api, headers: dict[str, str]) -> str:
    files = {"file": ("Other.png", (SAMPLES / "screenshot.png").read_bytes(), "x/y")}
    response = await api.client.post(f"{PREFIX}/documents", files=files, headers=headers)
    assert response.status_code == 202, response.text
    await api.drain()
    return str(response.json()["id"])


async def test_a_change_sets_off_the_rules_that_become_true(api: Api) -> None:
    admin, owner = await api.admin(), await api.user()
    a, o = auth(admin), auth(owner)
    telekom = await post(api, "/contacts", {"name": "Telekom"}, a)
    vodafone = await post(api, "/contacts", {"name": "Vodafone"}, a)
    drawer = await post(api, "/drawers", {"name": "Household"}, o)
    await post(
        api,
        "/rules",
        {
            "name": "Telekom to household",
            "triggers": ["change"],
            "conditions": {"all": [{"field": "contact", "op": "is", "value": telekom["id"]}]},
            "actions": [{"type": "set_drawer", "drawer_id": drawer["id"]}],
        },
        o,
    )
    id = await upload(api, o)
    url = f"{PREFIX}/documents/{id}"
    response = await api.client.patch(url, json={"contact_id": vodafone["id"]}, headers=o)
    assert response.json()["rules"] == []
    response = await api.client.patch(url, json={"contact_id": telekom["id"]}, headers=o)
    assert response.status_code == 200, response.text
    changed = response.json()
    assert changed["drawer_id"] == drawer["id"]
    assert changed["lane"] == "green"
    assert [effect["field"] for effect in changed["rules"][0]["applied"]] == ["drawer"]
    # Not again: the rule held before this change.
    response = await api.client.patch(url, json={"title": "Bill"}, headers=o)
    assert response.json()["rules"] == []


async def test_a_rule_is_applied_to_existing_documents(api: Api) -> None:
    admin, owner = await api.admin(), await api.user()
    a, o = auth(admin), auth(owner)
    tax = await post(api, "/tags", {"name": "tax"}, a)
    first, second = await upload(api, o), await upload_other(api, o)
    await api.client.patch(f"{PREFIX}/documents/{first}", json={"title": "Mine"}, headers=o)
    rule = await post(
        api,
        "/rules",
        {
            "name": "Tax",
            "conditions": {"all": [{"field": "channel", "op": "is", "value": "api"}]},
            "actions": [
                {"type": "add_tags", "tag_ids": [tax["id"]]},
                {"type": "set_title", "template": "Tax {filename}"},
            ],
        },
        o,
    )
    response = await api.client.post(
        f"{PREFIX}/rules/{rule['id']}/apply/preview", json={}, headers=o
    )
    assert response.status_code == 200, response.text
    preview = response.json()
    assert {item["document_id"] for item in preview["items"]} == {first, second}
    items = {item["document_id"]: item for item in preview["items"]}
    assert [change["field"] for change in items[first]["changes"]] == ["tags"]
    assert [conflict["field"] for conflict in items[first]["conflicts"]] == ["title"]
    assert [change["field"] for change in items[second]["changes"]] == ["tags", "title"]

    response = await api.client.post(
        f"{PREFIX}/rules/{rule['id']}/apply",
        json={"version": 1, "document_ids": [first, second], "accept_conflicts": [first]},
        headers=o,
    )
    assert response.status_code == 202, response.text
    while await api.services.rule_applications.run_next_job():
        pass
    progress = await api.client.get(response.headers["location"], headers=o)
    assert progress.json()["status"] == "done", progress.text
    assert progress.json()["applied"] == 2
    one = (await api.client.get(f"{PREFIX}/documents/{first}", headers=o)).json()
    two = (await api.client.get(f"{PREFIX}/documents/{second}", headers=o)).json()
    assert one["title"] == "Tax Bill 2026" and one["tag_ids"] == [tax["id"]]
    assert two["title"] == "Tax Other" and two["tag_ids"] == [tax["id"]]


async def test_a_dry_run_shows_what_a_change_would_do(api: Api) -> None:
    admin, owner = await api.admin(), await api.user()
    telekom = await post(api, "/contacts", {"name": "Telekom"}, auth(admin))
    drawer = await post(api, "/drawers", {"name": "Household"}, auth(owner))
    await post(
        api,
        "/rules",
        {
            "name": "Telekom",
            "conditions": {"all": [{"field": "contact", "op": "is", "value": telekom["id"]}]},
            "actions": [{"type": "set_drawer", "drawer_id": drawer["id"]}],
        },
        auth(owner),
    )
    id = await upload(api, auth(owner))
    response = await api.client.post(
        f"{PREFIX}/documents/{id}/dry-run",
        json={"contact_id": telekom["id"]},
        headers=auth(owner),
    )
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["changed"] == ["contact", "drawer"]
    assert preview["visibility"]["drawer_id"] == drawer["id"]
    assert preview["visibility"]["shares"] == []
    stored = (await api.client.get(f"{PREFIX}/documents/{id}", headers=auth(owner))).json()
    assert stored["contact_id"] is None


async def test_an_editor_whose_change_files_the_document_away_learns_nothing_more(
    api: Api,
) -> None:
    admin, owner, editor = await api.admin(), await api.user(), await api.user()
    o = auth(owner)
    acme = await post(api, "/contacts", {"name": "ACME"}, auth(admin))
    shared = await post(api, "/drawers", {"name": "Shared"}, o)
    private = await post(api, "/drawers", {"name": "Private"}, o)
    response = await api.client.put(
        f"{PREFIX}/drawers/{shared['id']}/shares/{editor.id}",
        json={"level": "read_write"},
        headers=o,
    )
    assert response.status_code < 300, response.text
    for name, triggers, condition, drawer in (
        ("Shared", ["ingest"], {"field": "channel", "op": "is", "value": "api"}, shared),
        ("Private", ["change"], {"field": "contact", "op": "is", "value": acme["id"]}, private),
    ):
        await post(
            api,
            "/rules",
            {
                "name": name,
                "triggers": triggers,
                "conditions": {"all": [condition]},
                "actions": [{"type": "set_drawer", "drawer_id": drawer["id"]}],
            },
            o,
        )
    id = await upload(api, o)
    url = f"{PREFIX}/documents/{id}"
    assert (await api.client.get(url, headers=o)).json()["drawer_id"] == shared["id"]

    response = await api.client.patch(url, json={"contact_id": acme["id"]}, headers=auth(editor))

    assert response.status_code == 200, response.text
    assert response.json() == {"id": id, "access": None}
    assert (await api.client.get(url, headers=auth(editor))).status_code == 404
    assert (await api.client.get(url, headers=o)).json()["drawer_id"] == private["id"]
