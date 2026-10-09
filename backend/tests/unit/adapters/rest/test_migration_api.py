"""What a migration needs from the API: uploading for another owner with the metadata of the
source system, drawers and users created by an admin's token, the original's hash."""

import hashlib
import json
from typing import Any
from uuid import UUID

import pytest

from papiq.adapters.inbound.rest import PREFIX
from papiq.core.domain.documents import Document
from papiq.core.domain.errors import UnprocessableDocumentError
from papiq.core.domain.identity import TokenScope
from papiq.core.domain.pipeline import Step, StepResult
from papiq.core.domain.users import User
from papiq.core.services.imports import ImportedClassifyStep, ImportedExtractStep
from papiq.core.services.pipeline import DeferredResult, PlaceholderStep, StepExecutor
from tests.api import auth, bearer
from tests.contracts.processing import SAMPLES
from tests.unit.adapters.rest.conftest import Api

DOCUMENTS = f"{PREFIX}/documents"


@pytest.fixture(autouse=True)
def imported_steps(api: Api) -> None:
    """The classification steps as the container builds them, around steps that do nothing
    (there is no language model)."""
    uow = api.container.unit_of_work
    executors = api.services.pipeline._executors
    executors[Step.CLASSIFY] = ImportedClassifyStep(uow, PlaceholderStep())
    executors[Step.EXTRACT_ATTRIBUTES] = ImportedExtractStep(uow, PlaceholderStep())


def file(name: str = "scan.pdf") -> dict[str, tuple[str, bytes, str]]:
    return {"file": (name, (SAMPLES / name).read_bytes(), "x/y")}


async def post(api: Api, path: str, body: Any, headers: dict[str, str]) -> Any:
    response = await api.client.post(f"{PREFIX}{path}", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


async def upload(
    api: Api, headers: dict[str, str], *, name: str = "scan.pdf", **fields: str
) -> Any:
    return await api.client.post(DOCUMENTS, files=file(name), data=fields, headers=headers)


async def master_data(api: Api, admin: User) -> dict[str, Any]:
    a = auth(admin)
    return {
        "contact": await post(api, "/contacts", {"name": "ACME Energy"}, a),
        "type": await post(api, "/document-types", {"name": "Invoice"}, a),
        "tag": await post(api, "/tags", {"name": "tax"}, a),
        "amount": await post(api, "/attributes", {"name": "Amount", "data_type": "amount"}, a),
        "note": await post(api, "/attributes", {"name": "Notes", "data_type": "text"}, a),
    }


def metadata(data: dict[str, Any], **changes: Any) -> str:
    body = {
        "title": "Electricity March",
        "contact_id": data["contact"]["id"],
        "document_type_id": data["type"]["id"],
        "tag_ids": [data["tag"]["id"]],
        "document_date": "2026-03-31",
        "attributes": {
            data["amount"]["id"]: {"amount": "84.20", "currency": "EUR"},
            data["note"]["id"]: "paid by transfer",
        },
    } | changes
    return json.dumps(body)


# --- owner and metadata -----------------------------------------------------------------------


async def test_an_admin_uploads_for_another_owner_with_the_metadata(api: Api) -> None:
    admin, owner = await api.admin(), await api.user()
    data = await master_data(api, admin)
    response = await upload(
        api, auth(admin), channel="migration", owner=str(owner.id), metadata=metadata(data)
    )
    assert response.status_code == 202, response.text
    await api.drain()

    document = (await api.client.get(response.json()["status_url"], headers=auth(owner))).json()
    assert document["owner_id"] == str(owner.id)
    assert document["channel"] == "migration"
    assert (document["lane"], document["processing"]["status"]) == ("green", "completed")
    assert document["title"] == "Electricity March"
    assert document["contact_id"] == data["contact"]["id"]
    assert document["document_type_id"] == data["type"]["id"]
    assert document["tag_ids"] == [data["tag"]["id"]]
    assert document["document_date"] == "2026-03-31"
    assert document["attributes"] == {
        data["amount"]["id"]: {"amount": "84.20", "currency": "EUR"},
        data["note"]["id"]: "paid by transfer",
    }
    drawers = (await api.client.get(f"{PREFIX}/drawers", headers=auth(owner))).json()
    default = next(item for item in drawers if item["is_default"])
    assert document["drawer_id"] == default["id"]

    log = (await api.client.get(f"{DOCUMENTS}/{document['id']}/log", headers=auth(owner))).json()
    receive = log[0]
    assert receive["output"]["uploaded_by"] == str(admin.id)
    assert receive["output"]["imported"]["title"] == "Electricity March"
    applied = {
        entry["step"]: entry for entry in log if entry["step"] in ("classify", "extract_attributes")
    }
    assert applied["classify"]["outcome"] == "ok"
    assert applied["classify"]["reason"] == "taken over from the source system"
    assert applied["classify"]["model_version"] == "imported"
    assert applied["extract_attributes"]["model_version"] == "imported"
    # The admin does not own it.
    other = await api.client.get(f"{DOCUMENTS}?limit=50", headers=auth(admin))
    assert other.json()["items"] == []


class FailsOnce:
    """An OCR that cannot process the first document it sees (red at once), then works."""

    def __init__(self, inner: StepExecutor) -> None:
        self.inner = inner
        self.calls = 0

    async def run(self, document: Document) -> StepResult | DeferredResult:
        self.calls += 1
        if self.calls == 1:
            raise UnprocessableDocumentError("the file is damaged or not a valid document")
        return await self.inner.run(document)


async def test_a_red_document_keeps_the_metadata_and_gets_them_when_retried(api: Api) -> None:
    """The metadata is applied by the classification steps, so a document whose OCR fails is
    red without it; the receive entry keeps it, and a retry applies it (decision 8, M13)."""
    executors = api.services.pipeline._executors
    executors[Step.OCR] = FailsOnce(executors[Step.OCR])
    admin, owner = await api.admin(), await api.user()
    data = await master_data(api, admin)
    response = await upload(
        api, auth(admin), channel="migration", owner=str(owner.id), metadata=metadata(data)
    )
    assert response.status_code == 202, response.text
    await api.drain()
    url = response.json()["status_url"]
    document = (await api.client.get(url, headers=auth(owner))).json()
    assert (document["lane"], document["processing"]["current_step"]) == ("red", "ocr")
    assert (document["title"], document["contact_id"], document["attributes"]) == (
        "scan",
        None,
        {},
    )
    log = (await api.client.get(f"{DOCUMENTS}/{document['id']}/log", headers=auth(owner))).json()
    assert log[0]["output"]["imported"]["contact_id"] == data["contact"]["id"]

    retried = await api.client.post(f"{DOCUMENTS}/{document['id']}/retry", headers=auth(owner))
    assert retried.status_code == 202, retried.text
    await api.drain()
    document = (await api.client.get(url, headers=auth(owner))).json()
    assert (document["lane"], document["processing"]["status"]) == ("green", "completed")
    assert document["title"] == "Electricity March"
    assert document["contact_id"] == data["contact"]["id"]
    assert document["document_type_id"] == data["type"]["id"]
    assert document["tag_ids"] == [data["tag"]["id"]]
    assert document["document_date"] == "2026-03-31"
    assert document["attributes"][data["amount"]["id"]] == {"amount": "84.20", "currency": "EUR"}


async def test_the_original_hash_is_in_the_details(api: Api) -> None:
    owner = await api.user()
    response = await upload(api, auth(owner))
    document = (await api.client.get(response.json()["status_url"], headers=auth(owner))).json()
    assert document["sha256"] == hashlib.sha256((SAMPLES / "scan.pdf").read_bytes()).hexdigest()


async def test_the_owner_needs_write_access_to_the_drawer(api: Api) -> None:
    admin, owner, other = await api.admin(), await api.user(), await api.user()
    theirs = await post(api, "/drawers", {"name": "Work"}, auth(other))
    response = await upload(
        api, auth(admin), owner=str(owner.id), drawer_id=theirs["id"], channel="migration"
    )
    assert response.status_code in (403, 404)
    mine = await post(api, "/drawers", {"name": "Work", "owner_id": str(owner.id)}, auth(admin))
    assert mine["owner_id"] == str(owner.id)
    response = await upload(
        api, auth(admin), owner=str(owner.id), drawer_id=mine["id"], channel="migration"
    )
    assert response.status_code == 202, response.text
    document = (await api.client.get(response.json()["status_url"], headers=auth(owner))).json()
    assert document["drawer_id"] == mine["id"]


async def test_the_duplicate_check_is_the_owners(api: Api) -> None:
    admin, one, two = await api.admin(), await api.user(), await api.user()
    first = await upload(api, auth(admin), owner=str(one.id))
    assert first.status_code == 202
    again = await upload(api, auth(admin), owner=str(one.id))
    assert again.status_code == 409
    assert again.json()["existing_document_id"] == first.json()["id"]
    assert (await upload(api, auth(admin), owner=str(two.id))).status_code == 202
    assert (await upload(api, auth(admin))).status_code == 202  # the admin's own copy


async def test_only_admins_choose_the_owner_or_give_metadata(api: Api) -> None:
    admin, user, owner = await api.admin(), await api.user(), await api.user()
    data = await master_data(api, admin)
    response = await upload(api, auth(user), owner=str(owner.id))
    assert response.status_code == 403
    response = await upload(api, auth(user), channel="migration", metadata=metadata(data))
    assert response.status_code == 403
    # Naming themselves is no change.
    assert (await upload(api, auth(user), owner=str(user.id))).status_code == 202


async def test_an_owner_who_is_gone_or_inactive_is_not_found(api: Api) -> None:
    admin, owner = await api.admin(), await api.user()
    unknown = await upload(api, auth(admin), owner=str(UUID(int=5)))
    assert unknown.status_code == 404
    await api.services.users.set_active(admin.id, owner.id, False)
    inactive = await upload(api, auth(admin), owner=str(owner.id))
    assert inactive.status_code == 404
    assert (await upload(api, auth(admin), owner="not-a-uuid")).status_code == 422


async def test_a_deactivated_admin_and_a_read_token_can_neither(api: Api) -> None:
    admin, owner, boss = await api.admin(), await api.user(), await api.admin()
    _, token = await api.services.auth.create_api_token(admin.id, "read", TokenScope.READ)
    response = await upload(api, bearer(token), owner=str(owner.id))
    assert response.status_code == 403
    await api.services.users.set_active(boss.id, admin.id, False)
    response = await upload(api, auth(admin), owner=str(owner.id))
    assert response.status_code == 401


async def test_metadata_needs_the_channel_and_must_fit_the_master_data(api: Api) -> None:
    admin, owner = await api.admin(), await api.user()
    data = await master_data(api, admin)
    headers = auth(admin)
    for fields, text in [
        ({"metadata": metadata(data)}, "channel 'migration'"),
        ({"channel": "migration", "metadata": "{"}, "metadata"),
        (
            {"channel": "migration", "metadata": metadata(data, contact_id=str(UUID(int=9)))},
            "contact",
        ),
        (
            {"channel": "migration", "metadata": metadata(data, tag_ids=[str(UUID(int=9))])},
            "tag",
        ),
        (
            {"channel": "migration", "metadata": metadata(data, attributes={str(UUID(int=9)): 1})},
            "attribute",
        ),
        (
            {
                "channel": "migration",
                "metadata": metadata(data, attributes={data["amount"]["id"]: "12"}),
            },
            "attribute",
        ),
        ({"channel": "migration", "metadata": metadata(data, unknown=1)}, "unknown"),
    ]:
        response = await upload(api, headers, owner=str(owner.id), **fields)
        assert response.status_code == 422, (fields, response.text)
        assert text in response.json()["detail"], response.json()
    # Nothing was stored.
    assert (await api.client.get(f"{DOCUMENTS}?all_users=true", headers=headers)).json()[
        "items"
    ] == []


async def test_metadata_may_be_long(api: Api) -> None:
    admin, owner = await api.admin(), await api.user()
    data = await master_data(api, admin)
    notes = {data["note"]["id"]: "x" * 20_000}
    response = await upload(
        api,
        auth(admin),
        owner=str(owner.id),
        channel="migration",
        metadata=metadata(data, attributes=notes),
    )
    assert response.status_code == 202, response.text
    too_long = {data["note"]["id"]: "x" * 60_000}
    response = await upload(
        api,
        auth(admin),
        name="photo.jpg",
        owner=str(owner.id),
        channel="migration",
        metadata=metadata(data, attributes=too_long),
    )
    assert response.status_code == 400


async def test_a_migrated_document_without_metadata_is_processed_as_usual(api: Api) -> None:
    admin = await api.admin()
    response = await upload(api, auth(admin), channel="migration")
    assert response.status_code == 202
    await api.drain()
    document = (await api.client.get(response.json()["status_url"], headers=auth(admin))).json()
    assert (document["channel"], document["lane"]) == ("migration", "green")
    assert document["title"] == "scan"


async def test_reprocessing_applies_the_metadata_again(api: Api) -> None:
    admin, owner = await api.admin(), await api.user()
    data = await master_data(api, admin)
    response = await upload(
        api, auth(admin), channel="migration", owner=str(owner.id), metadata=metadata(data)
    )
    await api.drain()
    url = response.json()["status_url"]
    await api.client.patch(url, json={"title": "Changed", "tag_ids": []}, headers=auth(owner))
    reprocess = await api.client.post(
        f"{url}/reprocess", json={"from_step": "classify"}, headers=auth(owner)
    )
    assert reprocess.status_code == 202, reprocess.text
    await api.drain()
    document = (await api.client.get(url, headers=auth(owner))).json()
    assert document["title"] == "Electricity March"
    assert document["tag_ids"] == [data["tag"]["id"]]
    assert document["lane"] == "green"


# --- drawers and users ------------------------------------------------------------------------


async def test_drawers_for_another_owner(api: Api) -> None:
    admin, owner, other = await api.admin(), await api.user(), await api.user()
    created = await api.client.post(
        f"{PREFIX}/drawers", json={"name": "Shared", "owner_id": str(owner.id)}, headers=auth(admin)
    )
    assert created.status_code == 201
    assert created.json()["owner_id"] == str(owner.id)
    # The name is unique per owner, not across owners.
    again = await api.client.post(
        f"{PREFIX}/drawers", json={"name": "shared", "owner_id": str(owner.id)}, headers=auth(admin)
    )
    assert again.status_code == 409
    own = await api.client.post(f"{PREFIX}/drawers", json={"name": "Shared"}, headers=auth(admin))
    assert own.status_code == 201
    # Others may not, naming themselves is no change.
    refused = await api.client.post(
        f"{PREFIX}/drawers", json={"name": "X", "owner_id": str(owner.id)}, headers=auth(other)
    )
    assert refused.status_code == 403
    mine = await api.client.post(
        f"{PREFIX}/drawers", json={"name": "X", "owner_id": str(other.id)}, headers=auth(other)
    )
    assert mine.status_code == 201
    unknown = await api.client.post(
        f"{PREFIX}/drawers", json={"name": "Y", "owner_id": str(UUID(int=4))}, headers=auth(admin)
    )
    assert unknown.status_code == 404
    shared = await api.client.put(
        f"{PREFIX}/drawers/{created.json()['id']}/shares/{other.id}",
        json={"level": "read"},
        headers=auth(admin),
    )
    assert shared.status_code == 200


async def test_an_admin_token_creates_users_without_password(api: Api) -> None:
    admin, user = await api.admin(), await api.user()
    created = await api.client.post(
        f"{PREFIX}/users", json={"username": "newcomer"}, headers=auth(admin)
    )
    assert created.status_code == 201, created.text
    assert created.json()["role"] == "user"
    for body in (
        {"username": "pw", "password": "a-long-enough-password"},
        {"username": "boss", "role": "admin"},
    ):
        refused = await api.client.post(f"{PREFIX}/users", json=body, headers=auth(admin))
        assert refused.status_code == 403, body
    assert (
        await api.client.post(f"{PREFIX}/users", json={"username": "x"}, headers=auth(user))
    ).status_code == 403
    _, read = await api.services.auth.create_api_token(admin.id, "r", TokenScope.READ)
    assert (
        await api.client.post(f"{PREFIX}/users", json={"username": "y"}, headers=bearer(read))
    ).status_code == 403
    # With a session, a password and the role admin work as before.
    async with api.sign_in(admin) as session:
        response = await session.client.post(
            f"{PREFIX}/users",
            json={"username": "root", "role": "admin", "password": "a-long-enough-password"},
            headers=session.headers,
        )
        assert response.status_code == 201, response.text


async def test_rules_run_and_trust_the_imported_values(api: Api) -> None:
    admin, owner, reader = await api.admin(), await api.user(), await api.user()
    data = await master_data(api, admin)
    shared = await post(api, "/drawers", {"name": "Shared"}, auth(owner))
    await api.client.put(
        f"{PREFIX}/drawers/{shared['id']}/shares/{reader.id}",
        json={"level": "read"},
        headers=auth(owner),
    )
    await post(
        api,
        "/rules",
        {
            "name": "Energy",
            "conditions": {
                "all": [
                    {"field": "channel", "op": "is", "value": "migration"},
                    {"field": "contact", "op": "is", "value": data["contact"]["id"]},
                ]
            },
            "actions": [{"type": "set_drawer", "drawer_id": shared["id"]}],
        },
        auth(owner),
    )
    response = await upload(
        api, auth(admin), channel="migration", owner=str(owner.id), metadata=metadata(data)
    )
    await api.drain()
    document = (await api.client.get(response.json()["status_url"], headers=auth(owner))).json()
    # Taken over from the source system, not guessed by a model: the rule files it for sharing.
    assert (document["lane"], document["drawer_id"]) == ("green", shared["id"])
