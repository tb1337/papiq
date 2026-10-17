"""Inbox, review and confirmation over HTTP."""

from uuid import UUID

from papiq.adapters.inbound.rest import PREFIX
from papiq.core.domain.classification import (
    Candidate,
    CandidateSource,
    FieldCheck,
    checks_to_json,
)
from papiq.core.domain.master_data import Contact
from papiq.core.domain.pipeline import Node, NodeResult, Outcome
from tests.api import auth
from tests.builders import NOW
from tests.unit.adapters.rest.conftest import Api
from tests.unit.adapters.rest.test_documents import pdf
from tests.unit.services.conftest import Returns

PROBLEM = "application/problem+json"
NEIGHBOUR = UUID("01999d5e-3333-7c1e-b6a3-2f4d5e6f7a8b")


def uncertain(contact: Contact) -> NodeResult:
    checks = [
        FieldCheck(
            field="contact",
            outcome=Outcome.UNCERTAIN,
            confidence=0.8,
            reason="'Stadtwerk' is similar to the contact 'Stadtwerke'",
            proposed="Stadtwerk",
            suggestion=str(contact.id),
            candidates=(
                Candidate(
                    value=str(contact.id),
                    label="Stadtwerke",
                    sources=(
                        CandidateSource(signal="signal_llm", score=0.8, quote="Stadtwerk"),
                        CandidateSource(
                            signal="signal_neighbours",
                            names=True,
                            score=2 / 3,
                            votes=2,
                            of=3,
                            documents=(str(NEIGHBOUR), str(NEIGHBOUR)),
                        ),
                        CandidateSource(
                            signal="signal_facts", identifiers=("iban",), named_in_text=True
                        ),
                    ),
                ),
            ),
        ),
        FieldCheck(
            field="document_date",
            outcome=Outcome.UNCERTAIN,
            confidence=0,
            reason="the date does not appear in the text",
            proposed="2026-03-31",
        ),
    ]
    return NodeResult(
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
    api.services.pipeline._executors[Node.JUDGE] = Returns(uncertain(contact))
    response = await api.client.post(f"{PREFIX}/documents", files=pdf(), headers=auth(owner))
    url = response.json()["status_url"]
    await api.drain()

    inbox = (await api.client.get(f"{PREFIX}/inbox", headers=auth(owner))).json()
    (item,) = inbox["items"]
    assert inbox["next_cursor"] is None
    assert item["document"]["lane"] == "yellow"
    assert item["document"]["processing"]["status"] == "review"
    (open,) = item["open"]
    assert (open["node"], open["outcome"]) == ("judge", "uncertain")
    assert [field["field"] for field in open["fields"]] == ["contact", "document_date"]
    assert open["fields"][0]["suggestion"] == str(contact.id)
    (candidate,) = open["fields"][0]["candidates"]
    assert (candidate["label"], candidate["contradicted"]) == ("Stadtwerke", False)
    llm, neighbours, facts = candidate["sources"]
    assert (llm["signal"], llm["names"], llm["score"], llm["quote"]) == (
        "signal_llm",
        False,
        0.8,
        "Stadtwerk",
    )
    assert (neighbours["votes"], neighbours["found"], neighbours["score"]) == (2, 3, 0.6667)
    assert neighbours["documents"] == [str(NEIGHBOUR)] * 2
    assert (facts["identifiers"], facts["named_in_text"]) == (["iban"], True)
    assert open["fields"][1]["candidates"] == []

    review = (await api.client.get(f"{url}/review", headers=auth(owner))).json()
    (classify,) = [node for node in review["nodes"] if node["node"] == "judge"]
    assert (classify["model_version"], classify["truncated"]) == ("fake-llm 1", False)
    assert classify["fields"][1]["proposed"] == "2026-03-31"

    undecided = await api.client.post(
        f"{url}/confirm", json={"accept_suggestions": True}, headers=auth(owner)
    )
    assert undecided.status_code == 422
    assert undecided.headers["content-type"] == PROBLEM
    assert undecided.json()["open_fields"] == ["document_date"]

    for body in ({"resume_at": "file"}, {"changes": {"space_id": "x"}}, {"other": 1}):
        invalid = await api.client.post(f"{url}/confirm", json=body, headers=auth(owner))
        assert invalid.status_code == 422, body

    confirmed = await api.client.post(
        f"{url}/confirm",
        json={"changes": {"document_date": "2026-03-31"}, "accept_suggestions": True},
        headers=auth(owner),
    )
    assert confirmed.status_code == 202, confirmed.text
    nodes = {node["node"]: node["status"] for node in confirmed.json()["processing"]["nodes"]}
    assert (nodes["fields"], nodes["apply_rules"]) == ("done", "waiting")
    await api.drain()
    document = (await api.client.get(url, headers=auth(owner))).json()
    assert document["lane"] == "green"
    assert (document["contact_id"], document["document_date"]) == (str(contact.id), "2026-03-31")
    assert (await api.client.get(f"{PREFIX}/inbox", headers=auth(owner))).json()["items"] == []

    again = await api.client.post(f"{url}/confirm", json={}, headers=auth(owner))
    assert again.status_code == 409
