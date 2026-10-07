"""Inbox, review and confirmation over HTTP."""

from papiq.adapters.inbound.rest import PREFIX
from papiq.core.domain.classification import FieldCheck, checks_to_json
from papiq.core.domain.master_data import Contact
from papiq.core.domain.pipeline import Outcome, Step, StepResult
from tests.api import auth
from tests.builders import NOW
from tests.unit.adapters.rest.conftest import Api
from tests.unit.adapters.rest.test_documents import pdf
from tests.unit.services.conftest import Returns

PROBLEM = "application/problem+json"


def uncertain(contact: Contact) -> StepResult:
    checks = [
        FieldCheck(
            field="contact",
            outcome=Outcome.UNCERTAIN,
            confidence=0.8,
            reason="'Stadtwerk' is similar to the contact 'Stadtwerke'",
            proposed="Stadtwerk",
            suggestion=str(contact.id),
        ),
        FieldCheck(
            field="document_date",
            outcome=Outcome.UNCERTAIN,
            confidence=0,
            reason="the date does not appear in the text",
            proposed="2026-03-31",
        ),
    ]
    return StepResult(
        outcome=Outcome.UNCERTAIN,
        reason="contact, document_date",
        model_version="fake-llm 1",
        input={"truncated": False},
        output={"fields": checks_to_json(checks)},
    )


async def test_confirm_from_the_inbox(api: Api) -> None:
    owner = await api.user()
    contact = Contact.create(name="Stadtwerke", now=NOW)
    async with api.container.unit_of_work() as uow:
        await uow.contacts.add(contact)
        await uow.commit()
    api.services.pipeline._executors[Step.CLASSIFY] = Returns(uncertain(contact))
    response = await api.client.post(f"{PREFIX}/documents", files=pdf(), headers=auth(owner))
    url = response.json()["status_url"]
    await api.drain()

    inbox = (await api.client.get(f"{PREFIX}/inbox", headers=auth(owner))).json()
    (item,) = inbox["items"]
    assert inbox["next_cursor"] is None
    assert item["document"]["lane"] == "yellow"
    assert item["document"]["processing"]["status"] == "review"
    (open,) = item["open"]
    assert (open["step"], open["outcome"]) == ("classify", "uncertain")
    assert [field["field"] for field in open["fields"]] == ["contact", "document_date"]
    assert open["fields"][0]["suggestion"] == str(contact.id)

    review = (await api.client.get(f"{url}/review", headers=auth(owner))).json()
    (classify,) = [step for step in review["steps"] if step["step"] == "classify"]
    assert (classify["model_version"], classify["truncated"]) == ("fake-llm 1", False)
    assert classify["fields"][1]["proposed"] == "2026-03-31"

    undecided = await api.client.post(
        f"{url}/confirm", json={"accept_suggestions": True}, headers=auth(owner)
    )
    assert undecided.status_code == 422
    assert undecided.headers["content-type"] == PROBLEM
    assert undecided.json()["open_fields"] == ["document_date"]

    for body in ({"resume_at": "file"}, {"changes": {"drawer_id": "x"}}, {"other": 1}):
        invalid = await api.client.post(f"{url}/confirm", json=body, headers=auth(owner))
        assert invalid.status_code == 422, body

    confirmed = await api.client.post(
        f"{url}/confirm",
        json={"changes": {"document_date": "2026-03-31"}, "accept_suggestions": True},
        headers=auth(owner),
    )
    assert confirmed.status_code == 202, confirmed.text
    assert confirmed.json()["processing"]["current_step"] == "apply_rules"
    await api.drain()
    document = (await api.client.get(url, headers=auth(owner))).json()
    assert document["lane"] == "green"
    assert (document["contact_id"], document["document_date"]) == (str(contact.id), "2026-03-31")
    assert (await api.client.get(f"{PREFIX}/inbox", headers=auth(owner))).json()["items"] == []

    again = await api.client.post(f"{url}/confirm", json={}, headers=auth(owner))
    assert again.status_code == 409
