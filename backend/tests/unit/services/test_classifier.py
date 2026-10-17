"""Training the classifier from confirmed documents, its jobs, its status and its prediction."""

import asyncio
from collections.abc import Mapping
from datetime import timedelta
from typing import Any

import pytest

from papiq.adapters.outbound.memory import FakeClassifier
from papiq.core.domain.classifier import KEPT_MODELS
from papiq.core.domain.documents import Document, DocumentChanges
from papiq.core.domain.errors import ConflictError, NotFoundError, PermissionDeniedError
from papiq.core.domain.events import ClassifierTrained
from papiq.core.domain.identifiers import Identifier, IdentifierKind, IdentifierSource
from papiq.core.domain.ids import ContactId
from papiq.core.domain.master_data import Assignment, Contact, Tag, TagScope
from papiq.core.domain.users import Role, User
from papiq.core.ports import UnitOfWork
from papiq.core.services.classifier import (
    CHECK_JOB,
    TRAIN_JOB,
    ClassifierPolicy,
    ClassifierService,
)
from papiq.core.services.master_data import replace_learned_identifiers
from papiq.core.services.objects import classifier_key, text_key
from tests import builders
from tests.unit.services.conftest import World

OWN_IBAN = "DE02 1203 0000 0000 2020 51"
THEIR_IBAN = "DE89 3704 0044 0532 0130 00"


def service(world: World, **policy: object) -> ClassifierService:
    values = {"min_examples": 4, "retrain_after": 2, **policy}
    return ClassifierService(
        world.uow,
        world.object_store,
        world.clock,
        FakeClassifier(),
        ClassifierPolicy(**values),  # type: ignore[arg-type]
    )


async def confirmed(world: World, owner: User, contact: Contact, text: str) -> Document:
    space = await world.default_space(owner)
    document = builders.processed(owner, space, content=text)
    document.apply_changes(DocumentChanges(contact_id=contact.id), {}, world.clock.now())
    document.mark_confirmed(world.clock.now())
    async with world.uow() as uow:
        await uow.documents.add(document)
        await uow.commit()
    await world.object_store.put(text_key(document.id), text.encode(), content_type="text/markdown")
    return document


async def corpus(world: World) -> tuple[User, Contact, Contact]:
    owner = await world.user()
    utility = Contact.create(name="Stadtwerke", now=builders.NOW)
    bank = Contact.create(name="Beispielbank", now=builders.NOW)
    async with world.uow() as uow:
        await uow.contacts.add(utility)
        await uow.contacts.add(bank)
        await uow.commit()
    for number in range(2):
        await confirmed(
            world,
            owner,
            utility,
            f"Stadtwerke Stromrechnung Zählerstand {number} IBAN {THEIR_IBAN} von {OWN_IBAN}",
        )
        await confirmed(world, owner, bank, f"Beispielbank Kontoauszug Saldo {number} {OWN_IBAN}")
    return owner, utility, bank


async def test_training_learns_from_the_confirmed_documents(world: World) -> None:
    _, utility, bank = await corpus(world)
    classifier = service(world)
    model = await classifier.train()
    assert (model.number, model.examples) == (1, 4)
    assert model.examples_until == world.clock.now()
    trained = [event for event in world.events() if isinstance(event, ClassifierTrained)]
    assert [(event.number, event.examples) for event in trained] == [(1, 4)]
    assert model.summary["learned_identifiers"] == 1
    assert await world.object_store.get(classifier_key(1))
    async with world.uow() as uow:
        learned = (await uow.contacts.get(utility.id)).identifiers
        # The owner's own IBAN stands in both contacts' documents: never learned.
        assert [(item.kind, item.source) for item in learned] == [
            (IdentifierKind.IBAN, IdentifierSource.LEARNED)
        ]
        assert (await uow.contacts.get(bank.id)).identifiers == []
    predicted = await classifier.predict("Stadtwerke Zählerstand Stromrechnung")
    assert predicted is not None
    assert predicted[1].contacts[0][0] == utility.id


async def test_only_the_tags_the_classification_sets_are_learned(world: World) -> None:
    owner, utility, _ = await corpus(world)
    electricity = Tag.create(name="Strom", now=builders.NOW)
    paid = Tag.create(name="Bezahlt", now=builders.NOW, assignment=Assignment.MANUAL)
    async with world.uow() as uow:
        await uow.tags.add(electricity)
        await uow.tags.add(paid)
        for document in await uow.documents.list_all():
            if document.contact_id == utility.id:
                tags = frozenset({electricity.id, paid.id})
                document.apply_changes(DocumentChanges(tag_ids=tags), {}, world.clock.now())
                await uow.documents.update(document)
        await uow.commit()
    classifier = service(world)
    await classifier.train()
    predicted = await classifier.predict("Stadtwerke Zählerstand Stromrechnung")
    assert predicted is not None
    assert set(predicted[1].tags) == {electricity.id}
    del owner


async def test_topics_are_learned_and_given_to_the_identifiers_of_one_topic(
    world: World,
) -> None:
    """The utility's documents carry "Strom", the bank's "Girokonto" (set by hand: not
    learned as a class). The utility's IBAN stands only in documents of "Strom": it points to
    it."""
    _, utility, bank = await corpus(world)
    power = Tag.create(name="Strom", now=builders.NOW, scope=TagScope.TOPIC)
    account = Tag.create(
        name="Girokonto", now=builders.NOW, scope=TagScope.TOPIC, assignment=Assignment.MANUAL
    )
    async with world.uow() as uow:
        await uow.tags.add(power)
        await uow.tags.add(account)
        for contact, topic in ((utility, power), (bank, account)):
            stored = await uow.contacts.get(contact.id)
            stored.set_topics([topic.id])
            await uow.contacts.update(stored)
        for document in await uow.documents.list_all():
            topic = power if document.contact_id == utility.id else account
            document.apply_changes(DocumentChanges(topic_id=topic.id), {}, world.clock.now())
            await uow.documents.update(document)
        await uow.commit()
    classifier = service(world)
    await classifier.train()
    async with world.uow() as uow:
        (learned,) = (await uow.contacts.get(utility.id)).identifiers
        assert (learned.kind, learned.topic_id) == (IdentifierKind.IBAN, power.id)
    predicted = await classifier.predict("Stadtwerke Zählerstand Stromrechnung")
    assert predicted is not None
    # One topic the classification sets is no target to tell apart.
    assert predicted[1].topics == []


async def test_two_trainings_at_once_take_two_numbers(world: World) -> None:
    """The worker's job and the command at the same time: one stores after the other."""
    await corpus(world)
    first, second = await asyncio.gather(service(world).train(), service(world).train())
    assert {first.number, second.number} == {1, 2}


async def test_a_contact_changed_meanwhile_stores_the_model_again(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An admin saves a contact while the learned identifiers are written: the version
    conflict repeats the step on the new state instead of losing the training."""
    _, utility, _ = await corpus(world)
    admin = await world.user(role=Role.ADMIN)
    calls = 0

    async def meanwhile(
        uow: UnitOfWork, learned: Mapping[ContactId, list[Identifier]], **options: Any
    ) -> int:
        """The learned identifiers are staged; then the admin's change commits first."""
        nonlocal calls
        calls += 1
        staged = await replace_learned_identifiers(uow, learned, **options)
        if calls == 1:
            await world.master_data.change_contact(admin.id, utility.id, aliases=["SWM"])
        return staged

    monkeypatch.setattr("papiq.core.services.classifier.replace_learned_identifiers", meanwhile)
    model = await service(world).train()
    assert (model.number, calls) == (1, 2)
    async with world.uow() as uow:
        contact = await uow.contacts.get(utility.id)
    assert contact.aliases == ["SWM"]
    assert [item.source for item in contact.identifiers] == [IdentifierSource.LEARNED]


async def test_old_models_go(world: World) -> None:
    await corpus(world)
    classifier = service(world)
    for _ in range(KEPT_MODELS + 1):
        latest = await classifier.train()
    assert latest.number == KEPT_MODELS + 1
    async with world.uow() as uow:
        numbers = [model.number for model in await uow.classifier_models.list_all()]
    assert numbers == list(range(KEPT_MODELS + 1, 1, -1))
    with pytest.raises(NotFoundError):
        await world.object_store.get(classifier_key(1))


async def test_the_check_queues_a_training_when_enough_was_confirmed(world: World) -> None:
    owner, utility, _ = await corpus(world)
    classifier = service(world, min_examples=5)
    assert not await classifier.check()
    await confirmed(world, owner, utility, "Stadtwerke Abschlag")
    assert await classifier.check()
    await classifier.schedule()
    assert await classifier.run_next_job()  # the check, which queues the next one
    queued = [job.kind for job in world.database.jobs.values() if job.status.is_active]
    assert TRAIN_JOB in queued and CHECK_JOB in queued
    assert await classifier.run_next_job()  # the training
    async with world.uow() as uow:
        assert (await uow.classifier_models.latest()) is not None
    # One confirmation since: not enough for another.
    world.clock.advance(timedelta(minutes=1))
    await confirmed(world, owner, utility, "Stadtwerke Jahresabrechnung")
    assert not await classifier.check()


async def test_status_and_training_on_request_are_for_admins(world: World) -> None:
    admin, user = await world.user(role=Role.ADMIN), await world.user()
    classifier = service(world)
    with pytest.raises(PermissionDeniedError):
        await classifier.status(user.id)
    with pytest.raises(PermissionDeniedError):
        await classifier.request_training(user.id)
    with pytest.raises(ConflictError):  # too few confirmed documents
        await classifier.request_training(admin.id)
    await corpus(world)
    await classifier.request_training(admin.id)
    await classifier.request_training(admin.id)  # queued once
    status = await classifier.status(admin.id)
    assert (status.model, status.confirmed, status.since) == (None, 4, 4)
