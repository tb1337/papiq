"""The classifier's status and training on request: admins only, 503 without a classifier."""

from dataclasses import replace

from papiq.adapters.inbound.rest import PREFIX
from papiq.core.domain.documents import DocumentChanges
from papiq.core.domain.master_data import Contact
from papiq.core.services.objects import text_key
from tests import builders
from tests.api import auth
from tests.unit.adapters.rest.conftest import Api


async def test_status_and_training(api: Api) -> None:
    admin, user = await api.admin(), await api.user()
    assert (await api.client.get(f"{PREFIX}/classifier", headers=auth(user))).status_code == 403
    response = await api.client.get(f"{PREFIX}/classifier", headers=auth(admin))
    assert response.status_code == 200
    assert response.json() == {
        "model": None,
        "confirmed": 0,
        "since": 0,
        "min_examples": 20,
        "retrain_after": 25,
        "threshold": 0.8,
    }
    train = f"{PREFIX}/classifier/train"
    assert (await api.client.post(train, headers=auth(user))).status_code == 403
    too_few = await api.client.post(train, headers=auth(admin))
    assert too_few.status_code == 409
    assert too_few.json()["detail"] == "0 confirmed documents; training needs 20"


async def test_without_a_classifier_the_endpoints_answer_503(api: Api) -> None:
    admin = await api.admin()
    api.app.state.context = replace(api.app.state.context, classifier=None)
    for response in (
        await api.client.get(f"{PREFIX}/classifier", headers=auth(admin)),
        await api.client.post(f"{PREFIX}/classifier/train", headers=auth(admin)),
    ):
        assert response.status_code == 503
        assert response.json()["detail"] == "no classifier is configured"


async def test_the_status_of_a_trained_model(api: Api) -> None:
    admin, owner = await api.admin(), await api.user()
    async with api.container.unit_of_work() as uow:
        space = await uow.spaces.get_default(owner.id)
        contacts = [Contact.create(name=name, now=builders.NOW) for name in ("Bank", "Werk")]
        for contact in contacts:
            await uow.contacts.add(contact)
        for number in range(4):
            contact = contacts[number % 2]
            document = builders.processed(owner, space, content=str(number))
            document.apply_changes(DocumentChanges(contact_id=contact.id), {}, builders.NOW)
            document.mark_confirmed(builders.NOW)
            await uow.documents.add(document)
            text = f"{contact.name} Schreiben Nummer {number}".encode()
            await api.container.object_store.put(
                text_key(document.id), text, content_type="text/markdown"
            )
        await uow.commit()
    assert api.services.classifier is not None
    await api.services.classifier.train()
    model = (await api.client.get(f"{PREFIX}/classifier", headers=auth(admin))).json()["model"]
    assert model["number"] == 1 and model["examples"] == 4
    assert model["classes"] == {"contact": 2, "document_type": 0, "tags": 0}
    assert (model["learned_identifiers"], model["accuracy"]) == (0, None)
    assert model["method"] == "fake classifier"
