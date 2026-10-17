"""The topic through the signals and the judge: what the language model is asked, what the
facts tell, and what the judge applies."""

from typing import Any

from papiq.core.domain.documents import UNSET, Document
from papiq.core.domain.identifiers import Identifier, IdentifierKind
from papiq.core.domain.json_value import JsonObject, JsonValue
from papiq.core.domain.master_data import Assignment, Contact, DocumentType, Tag, TagScope
from papiq.core.domain.pipeline import NodeResult, Outcome
from papiq.core.services.classification.signals import FactsResult, FactsSignal
from papiq.core.services.pipeline import MetadataResult
from tests.builders import NOW
from tests.unit.services.conftest import World
from tests.unit.services.test_classification import (
    checks,
    document_with,
    judge,
    model,
    output,
    proposal,
    signal_step,
)


def output_of(schema: JsonObject) -> Any:
    return schema


PAY_SLIP = """\
# Nord Versicherungsgruppe AG
Entgeltabrechnung März 2026

Personalnummer: 004711
Herrn Max Nord, Musterweg 1, 12345 Musterstadt

Hamburg, 31.03.2026
Gesamtbrutto | 5.166,67 €
"""


async def seed(world: World) -> tuple[Contact, Contact, dict[str, Tag]]:
    """Nord enables Arbeit and Krankenversicherung, its personnel number points to Arbeit;
    Süd enables Girokonto; Privat is set by hand."""
    topics = {
        name: Tag.create(name=name, now=NOW, scope=TagScope.TOPIC)
        for name in ("Arbeit", "Krankenversicherung", "Girokonto")
    }
    topics["Privat"] = Tag.create(
        name="Privat", now=NOW, scope=TagScope.TOPIC, assignment=Assignment.MANUAL
    )
    nord = Contact.create(
        name="Nord Versicherungsgruppe AG",
        now=NOW,
        topic_ids=[topics[name].id for name in ("Arbeit", "Krankenversicherung", "Privat")],
        identifiers=[
            Identifier.parse(IdentifierKind.PERSONNEL_NUMBER, "4711", topic_id=topics["Arbeit"].id)
        ],
    )
    sued = Contact.create(name="Süd Bank", now=NOW, topic_ids=[topics["Girokonto"].id])
    async with world.uow() as uow:
        for topic in topics.values():
            await uow.tags.add(topic)
        await uow.contacts.add(nord)
        await uow.contacts.add(sued)
        await uow.document_types.add(DocumentType.create(name="Lohnabrechnung", now=NOW))
        await uow.commit()
    return nord, sued, topics


def pay_slip(**changes: JsonValue) -> JsonObject:
    answer = proposal(
        sender={"value": "Nord Versicherungsgruppe AG", "evidence": "Nord Versicherungsgruppe AG"},
        document_type="Lohnabrechnung",
        tags=[],
        fields=[],
        topic="Arbeit",
    )
    return answer | changes


async def test_the_model_is_asked_for_a_topic_of_the_contacts_it_sees(world: World) -> None:
    await seed(world)
    fake = model(pay_slip())
    document = await document_with(world, PAY_SLIP)
    result = await signal_step(world, fake).run(document)
    assert result.outcome is Outcome.OK
    assert result.input["prompt"] == "propose-3"
    assert result.input["topics_listed"] == 2
    assert output(result)["proposal"]["topic"] == "Arbeit"
    (request,) = fake.requests
    # Only the listed contact's topics the classification sets, not one set by hand.
    assert "- Nord Versicherungsgruppe AG — topics: Arbeit; Krankenversicherung\n" in request.user
    assert "Girokonto" not in request.user and "Privat" not in request.user
    assert output_of(request.schema)["properties"]["topic"] == {
        "anyOf": [
            {"type": "string", "enum": ["Arbeit", "Krankenversicherung"]},
            {"type": "null"},
        ]
    }


async def test_the_facts_tell_the_topic_an_identifier_points_to(world: World) -> None:
    nord, _, topics = await seed(world)
    document = await document_with(world, PAY_SLIP)
    ran = await FactsSignal(world.uow, world.object_store).run(document)
    assert isinstance(ran, FactsResult)
    assert ran.output["identifiers"] == [
        {
            "contact_id": str(nord.id),
            "kind": "personnel_number",
            "value": "4711",
            "topic_id": str(topics["Arbeit"].id),
        }
    ]


async def test_the_judge_sets_the_topic_an_identifier_and_the_model_name(world: World) -> None:
    nord, _, topics = await seed(world)
    document = await document_with(world, PAY_SLIP)
    result = await judge(world, document, model(pay_slip()))
    assert isinstance(result, MetadataResult)
    topic = checks(result.result)["topic"]
    assert (topic.outcome, topic.value) == (Outcome.OK, str(topics["Arbeit"].id))
    assert result.changes.contact_id == nord.id
    assert result.changes.topic_id == topics["Arbeit"].id
    applied = await _apply(world, document, result)
    assert (applied.contact_id, applied.topic_id, applied.confirmed_at) == (
        nord.id,
        topics["Arbeit"].id,
        None,
    )


async def test_a_topic_the_model_alone_names_is_a_suggestion(world: World) -> None:
    _, _, topics = await seed(world)
    document = await document_with(world, PAY_SLIP.replace("Personalnummer: 004711\n", ""))
    result = await judge(world, document, model(pay_slip(topic="Krankenversicherung")))
    assert isinstance(result, MetadataResult)
    topic = checks(result.result)["topic"]
    assert (topic.outcome, topic.value, topic.suggestion) == (
        Outcome.OK,
        None,
        str(topics["Krankenversicherung"].id),
    )
    # A topic never makes the document uncertain, and one only suggested is not applied.
    assert "topic" not in (result.result.reason or "")
    assert result.changes.topic_id is UNSET


async def test_a_new_contact_that_does_not_enable_the_topic_clears_it(world: World) -> None:
    nord, sued, topics = await seed(world)
    document = await document_with(world, PAY_SLIP)
    async with world.uow() as uow:
        stored = await uow.documents.get(document.id)
        stored.contact_id, stored.topic_id = sued.id, topics["Girokonto"].id
        await uow.documents.update(stored)
        await uow.commit()
    result = await judge(world, document, model(pay_slip(topic=None)))
    assert isinstance(result, MetadataResult)
    assert result.changes.contact_id == nord.id
    applied = await _apply(world, document, result)
    assert (applied.contact_id, applied.topic_id) == (nord.id, None)


async def _apply(world: World, document: Document, result: MetadataResult) -> Document:
    async with world.uow() as uow:
        stored = await uow.documents.get(document.id)
        applied = await result.apply(uow, stored, NOW)
        assert isinstance(applied, NodeResult)
        assert applied.outcome is not Outcome.FAILED
        await uow.documents.update(stored)
        await uow.commit()
    return stored
