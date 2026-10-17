"""What a document shows of its contact, over HTTP, and attaching an identifier to a contact."""

import dataclasses
from collections.abc import AsyncIterator

import httpx2
import pytest

from papiq.adapters.inbound.rest import PREFIX
from papiq.adapters.outbound.memory import (
    FAKE_GIRO_CODE,
    FakeCodeReader,
    FakeParser,
    ManualClock,
)
from papiq.composition.container import build_memory_container, build_services
from papiq.core.domain.codes import Code
from papiq.core.domain.locations import Box
from tests import builders
from tests.api import auth
from tests.contracts.processing import SAMPLES
from tests.unit.adapters.rest.conftest import Api, make_app

TEXT = """\
Papiq Test GmbH
Kundennummer 2004-7718
IBAN DE89 3704 0044 0532 0130 00
Lastschrift von DE02 1203 0000 0000 2020 51
"""
# The GiroCode twice on page 1 and once on page 2, a bar code in between.
BAR_CODE = Code(page=1, format="Code 128", text="K-20047718", box=Box(0.1, 0.5, 0.27, 0.08))
CODES = [
    FAKE_GIRO_CODE,
    BAR_CODE,
    dataclasses.replace(FAKE_GIRO_CODE, box=Box(0.1, 0.1, 0.12, 0.085)),
    dataclasses.replace(FAKE_GIRO_CODE, page=2),
]


@pytest.fixture
async def facts_api() -> AsyncIterator[Api]:
    clock = ManualClock(builders.NOW)
    container = dataclasses.replace(
        build_memory_container(clock),
        parser=FakeParser(TEXT),
        codes=FakeCodeReader(CODES),
    )
    services = build_services(container)
    builders.skip_classification(services.pipeline)
    app = make_app(container, services)
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="https://papiq") as client:
        yield Api(container, services, app, client, clock)


async def upload(api: Api, headers: dict[str, str], *, drain: bool = True) -> str:
    files = {"file": ("scan.pdf", (SAMPLES / "scan.pdf").read_bytes(), "application/pdf")}
    response = await api.client.post(f"{PREFIX}/documents", files=files, headers=headers)
    assert response.status_code == 202, response.text
    if drain:
        await api.drain()
    return f"{PREFIX}/documents/{response.json()['id']}"


async def post(api: Api, path: str, body: object, headers: dict[str, str]) -> httpx2.Response:
    return await api.client.post(f"{PREFIX}{path}", json=body, headers=headers)


async def test_names_identifiers_and_codes_with_their_places(facts_api: Api) -> None:
    api = facts_api
    admin, owner = await api.admin(), await api.user()
    contact = await post(
        api,
        "/contacts",
        {
            "name": "Papiq Test GmbH",
            "identifiers": [{"kind": "iban", "value": "DE89370400440532013000"}],
        },
        auth(admin),
    )
    assert contact.status_code == 201, contact.text
    contact_id = contact.json()["id"]
    field = await post(
        api,
        "/fields",
        {"name": "Betrag", "data_type": "amount", "document_type_ids": None},
        auth(admin),
    )
    assert field.status_code == 201, field.text
    url = await upload(api, auth(owner))
    changed = await api.client.patch(
        url,
        json={"fields": {field.json()["id"]: {"amount": "123.45", "currency": "EUR"}}},
        headers=auth(owner),
    )
    assert changed.status_code == 200, changed.text

    response = await api.client.get(f"{url}/facts", headers=auth(owner))
    assert response.status_code == 200, response.text
    facts = response.json()
    (named,) = facts["named"]
    assert (named["contact_id"], named["in_text"], named["in_code"]) == (contact_id, True, True)
    assert [TEXT[span["start"] : span["end"]] for span in named["spans"]] == ["Papiq Test"]
    by_value = {item["value"]: item for item in facts["identifiers"]}
    iban = by_value["DE89370400440532013000"]
    assert (iban["contact_id"], iban["in_text"], iban["in_code"]) == (contact_id, True, True)
    customer = by_value["20047718"]
    assert customer["contact_id"] is None
    assert [TEXT[span["start"] : span["end"]] for span in customer["spans"]] == ["2004-7718"]
    # Only admins learn on how many contacts' documents a value stands.
    assert customer["other_contacts"] is None
    # Once for its format and text, with every place it stands.
    code, bar = facts["codes"]
    assert code["format"] == "QR Code"
    assert code["places"] == [
        {
            "page": 1,
            "boxes": [
                {"x": 0.74, "y": 0.78, "width": 0.12, "height": 0.085},
                {"x": 0.1, "y": 0.1, "width": 0.12, "height": 0.085},
            ],
        },
        {"page": 2, "boxes": [{"x": 0.74, "y": 0.78, "width": 0.12, "height": 0.085}]},
    ]
    assert (bar["format"], bar["text"], [place["page"] for place in bar["places"]]) == (
        "Code 128",
        "K-20047718",
        [1],
    )
    assert code["giro"] == {
        "name": "Papiq Test GmbH",
        "iban": "DE89370400440532013000",
        "bic": "MUSTDEM1XXX",
        "amount": {"amount": "123.45", "currency": "EUR"},
        "purpose": "Rechnung 4711",
    }
    assert code["identifiers"] == [{"kind": "iban", "value": "DE89370400440532013000"}]
    assert code["fields"] == [field.json()["id"]]

    as_admin = (await api.client.get(f"{url}/facts", headers=auth(admin))).json()
    assert {item["value"]: item["other_contacts"] for item in as_admin["identifiers"]} == {
        "DE89370400440532013000": 0,
        "20047718": 0,
        "DE02120300000000202051": 0,
    }


async def test_facts_need_read_access_and_a_text(facts_api: Api) -> None:
    api = facts_api
    owner, stranger = await api.user(), await api.user()
    url = await upload(api, auth(owner), drain=False)
    assert (await api.client.get(f"{url}/facts", headers=auth(owner))).status_code == 409
    await api.drain()
    assert (await api.client.get(f"{url}/facts", headers=auth(owner))).status_code == 200
    assert (await api.client.get(f"{url}/facts", headers=auth(stranger))).status_code == 404
    assert (await api.client.get(f"{url}/facts")).status_code == 401


async def test_an_admin_attaches_an_identifier_from_a_document(facts_api: Api) -> None:
    api = facts_api
    admin, owner = await api.admin(), await api.user()
    utility = (await post(api, "/contacts", {"name": "Stadtwerke"}, auth(admin))).json()
    bank = (await post(api, "/contacts", {"name": "Bank"}, auth(admin))).json()
    url = await upload(api, auth(owner))
    path = f"/contacts/{utility['id']}/identifiers"
    body = {"kind": "customer_number", "value": "2004-7718"}

    assert (await post(api, path, body, auth(owner))).status_code == 403
    attached = await post(api, path, body, auth(admin))
    assert attached.status_code == 200, attached.text
    assert attached.json()["identifiers"] == [
        {"kind": "customer_number", "value": "20047718", "source": "manual", "topic_id": None}
    ]
    # Counts at once in the document's facts.
    facts = (await api.client.get(f"{url}/facts", headers=auth(owner))).json()
    customer = next(item for item in facts["identifiers"] if item["value"] == "20047718")
    assert customer["contact_id"] == utility["id"]
    # Another contact has it entered by hand; a value of no such kind.
    taken = await post(api, f"/contacts/{bank['id']}/identifiers", body, auth(admin))
    assert taken.status_code == 409
    assert "Stadtwerke" in taken.json()["detail"]
    wrong = await post(api, path, {"kind": "iban", "value": "DE00 1234"}, auth(admin))
    assert wrong.status_code == 422
    # Attaching it again changes nothing.
    again = await post(api, path, body, auth(admin))
    assert again.json()["identifiers"] == attached.json()["identifiers"]
