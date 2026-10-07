"""Classification and attribute extraction with a fake language model: every check, the
answer handling, and the way through the pipeline."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from papiq.adapters.outbound.memory import FakeLanguageModel
from papiq.core.domain.attributes import AttributeDefinition, AttributeType, Money
from papiq.core.domain.classification import FieldCheck, attribute_field, checks_from_json
from papiq.core.domain.documents import UNSET, Document, DocumentChanges
from papiq.core.domain.errors import LanguageModelError
from papiq.core.domain.json_value import JsonObject
from papiq.core.domain.master_data import Contact, DocumentType, Tag
from papiq.core.domain.pipeline import Lane, Outcome, ProcessingStatus, Step, StepResult
from papiq.core.ports import StructuredRequest
from papiq.core.services.classification.steps import (
    NO_MODEL,
    ClassificationPolicy,
    ClassifyStep,
    ExtractAttributesStep,
)
from papiq.core.services.objects import markdown_key
from papiq.core.services.pipeline import MetadataResult, StepExecutor
from tests import builders
from tests.builders import NOW, incoming
from tests.unit.services.conftest import World

INVOICE = """\
# Stadtwerke Musterstadt GmbH
Postfach 12 34, 12345 Musterstadt

Herrn Max Beispiel, Musterweg 1, 12345 Musterstadt

Musterstadt, 31.03.2026

## Rechnung Nr. R-2026-0815

Stromverbrauch vom 01.01.2026 bis 31.03.2026

| Position | Betrag |
| --- | --- |
| Arbeitspreis | 70,76 € |
| Gesamtbetrag | 84,20 € |

Zahlbar bis 15.04.2026.
"""


@dataclass
class Seeded:
    contacts: dict[str, Contact]
    types: dict[str, DocumentType]
    tags: dict[str, Tag]
    attributes: dict[str, AttributeDefinition]


async def seed(world: World, contacts: tuple[str, ...] = ()) -> Seeded:
    names = contacts or ("Stadtwerke Musterstadt GmbH", "Stadtwerke Beispielstadt", "AOK Bayern")
    seeded = Seeded(
        contacts={name: Contact.create(name=name, now=NOW) for name in names},
        types={name: DocumentType.create(name=name, now=NOW) for name in ("Rechnung", "Vertrag")},
        tags={name: Tag.create(name=name, now=NOW) for name in ("Strom", "Versicherung")},
        attributes={},
    )
    invoice = seeded.types["Rechnung"].id
    for name, data_type, scope in [
        ("Rechnungsbetrag", AttributeType.AMOUNT, [invoice]),
        ("Rechnungsnummer", AttributeType.TEXT, [invoice]),
        ("Fällig am", AttributeType.DATE, [invoice]),
        ("Bezahlt", AttributeType.BOOLEAN, None),
    ]:
        seeded.attributes[name] = AttributeDefinition.create(
            name=name, data_type=data_type, document_type_ids=scope, now=NOW
        )
    async with world.uow() as uow:
        for contact in seeded.contacts.values():
            await uow.contacts.add(contact)
        for kind in seeded.types.values():
            await uow.document_types.add(kind)
        for tag in seeded.tags.values():
            await uow.tags.add(tag)
        for attribute in seeded.attributes.values():
            await uow.attributes.add(attribute)
        await uow.commit()
    return seeded


def classification(**changes: Any) -> JsonObject:
    answer: JsonObject = {
        "contact": {"value": "Stadtwerke Musterstadt GmbH", "evidence": None},
        "document_type": "Rechnung",
        "new_document_type": None,
        "tags": ["Strom"],
        "new_tags": [],
        "document_date": {"value": "2026-03-31", "evidence": "Musterstadt, 31.03.2026"},
    }
    return answer | changes


def extraction(**values: Any) -> JsonObject:
    """Answer for the attributes in key order: Bezahlt, Fällig am, Rechnungsbetrag,
    Rechnungsnummer (sorted by name)."""
    defaults: dict[str, Any] = {
        "a1": {"value": None, "evidence": None},
        "a2": {"value": "2026-04-15", "evidence": "Zahlbar bis 15.04.2026."},
        "a3": {
            "value": {"amount": "84.20", "currency": "EUR"},
            "evidence": "Gesamtbetrag | 84,20 €",
        },
        "a4": {"value": "R-2026-0815", "evidence": None},
    }
    return {"attributes": defaults | values}


def model(*answers: JsonObject | str) -> FakeLanguageModel:
    """Answers in turn; the last one again and again."""
    remaining = list(answers)

    def respond(request: StructuredRequest) -> JsonObject | str:
        return remaining.pop(0) if len(remaining) > 1 else remaining[0]

    return FakeLanguageModel(respond)


async def document_with(
    world: World, text: str = INVOICE, type: DocumentType | None = None
) -> Document:
    owner = await world.user()
    document = builders.document(owner, await world.default_drawer(owner))
    document.document_type_id = None if type is None else type.id
    await world.object_store.put(
        markdown_key(document.id), text.encode(), content_type="text/markdown; charset=utf-8"
    )
    return document


def classify_step(world: World, fake: FakeLanguageModel | None, **policy: Any) -> ClassifyStep:
    return ClassifyStep(
        world.uow, world.object_store, fake, world.clock, ClassificationPolicy(**policy)
    )


def extract_step(world: World, fake: FakeLanguageModel | None) -> ExtractAttributesStep:
    return ExtractAttributesStep(world.uow, world.object_store, fake, world.clock)


def checks(result: StepResult) -> dict[str, FieldCheck]:
    return {check.field: check for check in checks_from_json(result.output["fields"])}


async def classify(world: World, answer: JsonObject, text: str = INVOICE) -> MetadataResult:
    document = await document_with(world, text)
    result = await classify_step(world, model(answer)).run(document)
    assert isinstance(result, MetadataResult)
    return result


# --- classification ---------------------------------------------------------------------------


async def test_a_verified_classification_is_applied(world: World) -> None:
    seeded = await seed(world)
    fake = model(classification())
    document = await document_with(world)

    outcome = await classify_step(world, fake).run(document)

    assert isinstance(outcome, MetadataResult)
    result, changes = outcome.result, outcome.changes
    assert result.outcome is Outcome.OK
    assert result.confidence == 1
    assert result.model_version == "fake-llm 1"
    assert changes == DocumentChanges(
        contact_id=seeded.contacts["Stadtwerke Musterstadt GmbH"].id,
        document_type_id=seeded.types["Rechnung"].id,
        document_date=date(2026, 3, 31),
    )
    assert outcome.add_tags == frozenset({seeded.tags["Strom"].id})
    assert result.input["truncated"] is False
    assert result.input["model"] == "fake-llm 1"
    assert result.input["endpoint"] == "memory"
    assert result.input["contacts"] == 3
    assert result.output["attempts"] == 1
    assert result.output["answer"] == classification()
    assert set(checks(result)) == {"contact", "document_type", "tags", "document_date"}

    (request,) = fake.requests
    assert "- Rechnung\n- Vertrag" in request.user
    assert "- Strom\n- Versicherung" in request.user
    assert "Stadtwerke Beispielstadt" not in request.user  # contacts are matched in code
    assert "Postfach 12 34" in request.user
    assert request.schema_name == "classification"


@pytest.mark.parametrize(
    "name",
    ["Stadtwerke Musterstadt", "STADTWERKE MUSTERSTADT GMBH", "Stadtwerke Musterstdt GmbH"],
)
async def test_the_contact_is_matched_and_shown_in_the_text(world: World, name: str) -> None:
    seeded = await seed(world)
    result = await classify(world, classification(contact={"value": name, "evidence": None}))
    contact = checks(result.result)["contact"]
    assert contact.ok
    assert contact.confidence >= 0.9
    assert result.changes.contact_id == seeded.contacts["Stadtwerke Musterstadt GmbH"].id


async def test_an_unknown_contact_is_proposed_as_new(world: World) -> None:
    await seed(world)
    text = INVOICE.replace("Stadtwerke Musterstadt GmbH", "Elektro Huber")
    result = await classify(
        world, classification(contact={"value": "Elektro Huber", "evidence": None}), text
    )
    contact = checks(result.result)["contact"]
    assert contact.outcome is Outcome.UNCERTAIN
    assert contact.new_name == "Elektro Huber"
    assert contact.suggestion is None
    assert "a new contact is proposed" in (contact.reason or "")
    assert result.changes.contact_id is UNSET
    assert result.result.outcome is Outcome.UNCERTAIN
    assert (result.result.reason or "").startswith("contact: no contact matches 'Elektro Huber'")


async def test_a_contact_absent_from_the_text_is_only_suggested(world: World) -> None:
    seeded = await seed(world)
    result = await classify(
        world, classification(contact={"value": "AOK Bayern", "evidence": None})
    )
    contact = checks(result.result)["contact"]
    assert contact.outcome is Outcome.UNCERTAIN
    assert contact.reason == "the contact 'AOK Bayern' does not appear in the text"
    assert contact.suggestion == str(seeded.contacts["AOK Bayern"].id)
    assert contact.confidence == 0.5


async def test_a_similar_contact_is_suggested(world: World) -> None:
    seeded = await seed(world)
    result = await classify(
        world, classification(contact={"value": "Stadtwerk Musterstadt Netz", "evidence": None})
    )
    contact = checks(result.result)["contact"]
    assert contact.outcome is Outcome.UNCERTAIN
    assert contact.suggestion == str(seeded.contacts["Stadtwerke Musterstadt GmbH"].id)
    assert "is similar to the contact 'Stadtwerke Musterstadt GmbH'" in (contact.reason or "")
    assert 0.75 <= contact.confidence < 0.9


async def test_the_contact_itself_must_be_named(world: World) -> None:
    """A typo in the text matches the contact closely, but the contact is not named."""
    seeded = await seed(world)
    text = INVOICE.replace("Stadtwerke Musterstadt GmbH", "Stadtwerke Musterstad GmbH")
    result = await classify(
        world,
        classification(contact={"value": "Stadtwerke Musterstad GmbH", "evidence": None}),
        text,
    )
    contact = checks(result.result)["contact"]
    assert contact.outcome is Outcome.UNCERTAIN
    assert contact.suggestion == str(seeded.contacts["Stadtwerke Musterstadt GmbH"].id)
    assert contact.confidence == 0.5


async def test_ambiguous_contacts_are_uncertain(world: World) -> None:
    await seed(world, contacts=("Muster GmbH", "Muster AG"))
    text = INVOICE.replace("Stadtwerke Musterstadt GmbH", "Muster")
    result = await classify(
        world, classification(contact={"value": "Muster", "evidence": None}), text
    )
    contact = checks(result.result)["contact"]
    assert contact.outcome is Outcome.UNCERTAIN
    assert "several contacts" in (contact.reason or "")


async def test_no_contact(world: World) -> None:
    await seed(world)
    result = await classify(world, classification(contact={"value": None, "evidence": None}))
    assert checks(result.result)["contact"].reason == "no contact recognised"


@pytest.mark.parametrize(
    ("answer", "reason", "new_name"),
    [
        ({"document_type": "Mahnung"}, "'Mahnung' is not a document type", "Mahnung"),
        (
            {"document_type": None, "new_document_type": "Werbung"},
            "no document type fits; a new type 'Werbung' is proposed",
            "Werbung",
        ),
        ({"document_type": None}, "no document type recognised", None),
    ],
)
async def test_uncertain_document_types(
    world: World, answer: dict[str, Any], reason: str, new_name: str | None
) -> None:
    await seed(world)
    result = await classify(world, classification(**answer))
    kind = checks(result.result)["document_type"]
    assert kind.outcome is Outcome.UNCERTAIN
    assert (kind.reason or "").startswith(reason)
    assert kind.new_name == new_name
    assert result.changes.document_type_id is UNSET


async def test_document_types_are_matched_regardless_of_case(world: World) -> None:
    seeded = await seed(world)
    result = await classify(world, classification(document_type="rechnung"))
    assert result.changes.document_type_id == seeded.types["Rechnung"].id


async def test_new_tags_are_only_proposed(world: World) -> None:
    seeded = await seed(world)
    result = await classify(
        world, classification(tags=["Strom", "Energie", "Strom"], new_tags=["Abschlag"])
    )
    tags = checks(result.result)["tags"]
    assert tags.ok
    assert tags.value == [str(seeded.tags["Strom"].id)]
    assert tags.suggestion == ["Abschlag", "Energie"]
    assert result.result.outcome is Outcome.OK
    assert result.add_tags == frozenset({seeded.tags["Strom"].id})


@pytest.mark.parametrize(
    ("value", "evidence", "reason", "suggestion"),
    [
        ("2026-03-30", None, "the date does not appear in the text", "2026-03-30"),
        (
            "2026-03-31",
            "31.03.2025",
            "the quoted passage does not appear in the text",
            "2026-03-31",
        ),
        ("31.03.2026", None, "'31.03.2026' is not a date (YYYY-MM-DD)", None),
        ("2026-02-30", None, "'2026-02-30' is not a date (YYYY-MM-DD)", None),
        ("1850-03-31", None, "1850-03-31 is not plausible", None),
        ("2030-03-31", None, "2030-03-31 is not plausible", None),
        (None, None, "no date recognised", None),
    ],
)
async def test_uncertain_dates(
    world: World, value: str | None, evidence: str | None, reason: str, suggestion: str | None
) -> None:
    await seed(world)
    text = INVOICE + "\nGültig bis 31.03.2030, gegründet 31.03.1850\n"
    result = await classify(
        world, classification(document_date={"value": value, "evidence": evidence}), text
    )
    check = checks(result.result)["document_date"]
    assert check.outcome is Outcome.UNCERTAIN
    assert check.reason == reason
    assert check.suggestion == suggestion
    assert result.changes.document_date is UNSET


async def test_nothing_recognised_fails(world: World) -> None:
    await seed(world)
    document = await document_with(world)
    result = await classify_step(world, FakeLanguageModel()).run(document)
    assert isinstance(result, StepResult)
    assert result.outcome is Outcome.FAILED
    assert result.reason == "nothing recognised: no contact, document type or date"


async def test_an_unfit_answer_is_asked_again(world: World) -> None:
    await seed(world)
    fake = model("Sure! Here is the JSON.", classification())
    outcome = await classify_step(world, fake).run(await document_with(world))
    assert isinstance(outcome, MetadataResult)
    assert outcome.result.outcome is Outcome.OK
    assert outcome.result.output["attempts"] == 2
    first, second = fake.requests
    assert second.user.startswith(first.user)
    assert "Your previous answer was not valid: the answer is not JSON" in second.user


async def test_two_unfit_answers_fail_the_step(world: World) -> None:
    await seed(world)
    injected = classification() | {"drawer": "Shared", "owner": "mallory"}
    outcome = await classify_step(world, model(injected)).run(await document_with(world))
    assert isinstance(outcome, StepResult)
    assert outcome.outcome is Outcome.FAILED
    assert outcome.reason == (
        "the model's answer does not fit the schema: the answer: unexpected drawer, owner"
    )
    assert outcome.output["attempts"] == 2
    assert len(outcome.output["answers"]) == 2  # type: ignore[arg-type]


async def test_without_a_model_a_person_classifies(world: World) -> None:
    result = await classify_step(world, None).run(await document_with(world))
    assert result == StepResult(outcome=Outcome.UNCERTAIN, reason=NO_MODEL)


async def test_an_unreachable_model_raises_for_a_retry(world: World) -> None:
    def fail(request: StructuredRequest) -> str:
        raise LanguageModelError("cannot reach ollama:11434")

    with pytest.raises(LanguageModelError):
        await classify_step(world, FakeLanguageModel(fail)).run(await document_with(world))


async def test_long_texts_are_shortened_but_checked_in_full(world: World) -> None:
    await seed(world)
    text = (
        INVOICE.replace("Musterstadt, 31.03.2026\n", "")
        + "\n".join(f"Zeile {number}" for number in range(3000))
        + "\nMusterstadt, 31.03.2026\n"
    )
    fake = model(classification())
    outcome = await classify_step(world, fake, input_budget=2000).run(
        await document_with(world, text)
    )
    assert isinstance(outcome, MetadataResult)
    assert outcome.result.input["truncated"] is True
    sent = outcome.result.input["sent"]
    assert isinstance(sent, int)
    assert sent < 2200
    assert "characters left out" in fake.requests[0].user
    assert checks(outcome.result)["document_date"].ok


async def test_many_tags_list_those_named_in_the_text_first(world: World) -> None:
    await seed(world)
    async with world.uow() as uow:
        for number in range(10):
            await uow.tags.add(Tag.create(name=f"Aaa {number}", now=NOW))
        await uow.commit()
    fake = model(classification())
    text = INVOICE + "\nTarif: Strom Basis\n"
    await classify_step(world, fake, max_tags=3).run(await document_with(world, text))
    request = fake.requests[0]
    assert "- Strom" in request.user
    assert request.user.count("- Aaa") == 2


# --- attributes -------------------------------------------------------------------------------


async def extract(
    world: World, answer: JsonObject, text: str = INVOICE
) -> tuple[Seeded, MetadataResult]:
    seeded = await seed(world)
    document = await document_with(world, text, seeded.types["Rechnung"])
    result = await extract_step(world, model(answer)).run(document)
    assert isinstance(result, MetadataResult)
    return seeded, result


def attribute_check(seeded: Seeded, result: MetadataResult, name: str) -> FieldCheck:
    return checks(result.result)[attribute_field(seeded.attributes[name].id)]


async def test_verified_attributes_are_applied(world: World) -> None:
    seeded, result = await extract(world, extraction())
    assert result.result.outcome is Outcome.OK
    attributes = seeded.attributes
    assert result.changes.attributes == {
        attributes["Rechnungsbetrag"].id: Money(Decimal("84.20"), "EUR"),
        attributes["Rechnungsnummer"].id: "R-2026-0815",
        attributes["Fällig am"].id: date(2026, 4, 15),
    }
    paid = attribute_check(seeded, result, "Bezahlt")
    assert paid.ok and paid.value is None  # a global attribute may be missing
    assert result.result.input["attributes"] == {
        "a1": str(attributes["Bezahlt"].id),
        "a2": str(attributes["Fällig am"].id),
        "a3": str(attributes["Rechnungsbetrag"].id),
        "a4": str(attributes["Rechnungsnummer"].id),
    }


@pytest.mark.parametrize(
    ("answer", "name", "reason", "suggestion"),
    [
        (
            {"a3": {"value": {"amount": "99.99", "currency": "EUR"}, "evidence": None}},
            "Rechnungsbetrag",
            "'Rechnungsbetrag': the amount does not appear in the text",
            {"amount": "99.99", "currency": "EUR"},
        ),
        (
            {"a3": {"value": {"amount": "84.20", "currency": "USD"}, "evidence": None}},
            "Rechnungsbetrag",
            "'Rechnungsbetrag': the currency USD does not appear in the text",
            {"amount": "84.20", "currency": "USD"},
        ),
        (
            {"a3": {"value": "84,20 €", "evidence": None}},
            "Rechnungsbetrag",
            "'84,20 €' is not a valid value of 'Rechnungsbetrag' (amount)",
            None,
        ),
        (
            {"a4": {"value": None, "evidence": None}},
            "Rechnungsnummer",
            "'Rechnungsnummer' was not found",
            None,
        ),
        (
            {"a4": {"value": "R-2026-0816", "evidence": None}},
            "Rechnungsnummer",
            "'Rechnungsnummer': the text does not appear in the text",
            "R-2026-0816",
        ),
        (
            {"a4": {"value": "R-2026-0815", "evidence": "Rechnung Nr. R-2026-0815 vom"}},
            "Rechnungsnummer",
            "'Rechnungsnummer': the quoted passage does not appear in the text",
            "R-2026-0815",
        ),
        (
            {"a2": {"value": "2026-04-16", "evidence": None}},
            "Fällig am",
            "'Fällig am': the date does not appear in the text",
            "2026-04-16",
        ),
        (
            {"a1": {"value": True, "evidence": None}},
            "Bezahlt",
            "'Bezahlt': the quoted passage does not appear in the text",
            True,
        ),
        (
            {"a1": {"value": True, "evidence": "31"}},
            "Bezahlt",
            "'Bezahlt': the quoted passage does not appear in the text",
            True,
        ),
        (
            {"a4": {"value": "R-2026", "evidence": None}},
            "Rechnungsnummer",
            "'Rechnungsnummer': the text does not appear in the text",
            "R-2026",
        ),
        (
            {"a3": {"value": {"amount": "84.20", "currency": "BIS"}, "evidence": None}},
            "Rechnungsbetrag",
            "'Rechnungsbetrag': the currency BIS does not appear in the text",
            {"amount": "84.20", "currency": "BIS"},
        ),
    ],
)
async def test_uncertain_attributes(
    world: World, answer: dict[str, Any], name: str, reason: str, suggestion: object
) -> None:
    seeded, result = await extract(world, extraction(**answer))
    check = attribute_check(seeded, result, name)
    assert check.outcome is Outcome.UNCERTAIN
    assert check.reason == reason
    assert check.suggestion == suggestion
    assert seeded.attributes[name].id not in result.changes.attributes
    assert result.result.outcome is Outcome.UNCERTAIN


async def test_a_date_equal_to_the_document_date_is_only_suggested(world: World) -> None:
    """Models put the document date into a due date the text does not give."""
    seeded = await seed(world)
    document = await document_with(world, type=seeded.types["Rechnung"])
    document.document_date = date(2026, 3, 31)
    answer = extraction(a2={"value": "2026-03-31", "evidence": "Musterstadt, 31.03.2026"})
    result = await extract_step(world, model(answer)).run(document)
    assert isinstance(result, MetadataResult)
    check = attribute_check(seeded, result, "Fällig am")
    assert check.outcome is Outcome.UNCERTAIN
    assert check.reason == "'Fällig am': the same date as the document date"
    assert check.suggestion == "2026-03-31"
    assert seeded.attributes["Fällig am"].id not in result.changes.attributes


async def test_values_are_read_leniently(world: World) -> None:
    seeded, result = await extract(
        world,
        extraction(
            a1={"value": True, "evidence": "Zahlbar bis 15.04.2026."},
            a3={"value": {"amount": "84,2", "currency": "eur"}, "evidence": None},
        ),
    )
    attributes = seeded.attributes
    assert result.changes.attributes[attributes["Rechnungsbetrag"].id] == Money(
        Decimal("84.2"), "EUR"
    )
    assert result.changes.attributes[attributes["Bezahlt"].id] is True


async def test_no_attributes_no_question(world: World) -> None:
    await seed(world)
    document = await document_with(world)  # no type: only the global attribute applies
    async with world.uow() as uow:
        for attribute in await uow.attributes.list_all():
            if attribute.is_global:
                await uow.attributes.remove(attribute.id)
        await uow.commit()
    fake = model(extraction())
    result = await extract_step(world, fake).run(document)
    assert result == StepResult(outcome=Outcome.OK, output={"fields": []})
    assert fake.requests == []
    assert await extract_step(world, None).run(document) == result


async def test_attributes_without_a_model(world: World) -> None:
    seeded = await seed(world)
    document = await document_with(world, type=seeded.types["Rechnung"])
    result = await extract_step(world, None).run(document)
    assert result == StepResult(outcome=Outcome.UNCERTAIN, reason=NO_MODEL)


async def test_extraction_prompt_names_type_and_attributes(world: World) -> None:
    seeded = await seed(world)
    fake = model(extraction())
    await extract_step(world, fake).run(await document_with(world, type=seeded.types["Rechnung"]))
    (request,) = fake.requests
    assert 'The document is of type "Rechnung".' in request.user
    assert '- a3: "Rechnungsbetrag" (object with amount' in request.user
    assert request.schema_name == "attributes"


# --- through the pipeline ---------------------------------------------------------------------


def responder(
    classify: JsonObject, extract: JsonObject
) -> Callable[[StructuredRequest], JsonObject]:
    return lambda request: classify if request.schema_name == "classification" else extract


async def run(world: World, fake: FakeLanguageModel) -> tuple[Any, Document]:
    owner = await world.user()
    executors: dict[Step, StepExecutor] = {
        Step.CLASSIFY: classify_step(world, fake),
        Step.EXTRACT_ATTRIBUTES: extract_step(world, fake),
    }
    pipeline = world.pipeline(executors)
    document = await pipeline.receive(owner.id, incoming(b"%PDF-1.7\n%%EOF\n"), filename="a.pdf")
    await world.object_store.put(
        markdown_key(document.id), INVOICE.encode(), content_type="text/markdown; charset=utf-8"
    )
    await world.drain(pipeline)
    return owner, await world.documents.get(owner.id, document.id)


async def test_a_verified_document_is_filed_green(world: World) -> None:
    seeded = await seed(world)
    owner, document = await run(world, FakeLanguageModel(responder(classification(), extraction())))
    assert document.lane is Lane.GREEN
    assert document.contact_id == seeded.contacts["Stadtwerke Musterstadt GmbH"].id
    assert document.document_type_id == seeded.types["Rechnung"].id
    assert document.tag_ids == {seeded.tags["Strom"].id}
    assert document.document_date == date(2026, 3, 31)
    assert document.attributes[seeded.attributes["Rechnungsbetrag"].id] == Money(
        Decimal("84.20"), "EUR"
    )
    assert "document.filed" in world.event_types()
    log = await world.documents.processing_log(owner.id, document.id)
    classified = next(entry for entry in log if entry.step is Step.CLASSIFY)
    assert classified.result.output["answer"] == classification()
    assert len(checks(classified.result)) == 4


async def test_an_uncertain_document_waits_in_the_inbox(world: World) -> None:
    seeded = await seed(world)
    answer = classification(contact={"value": "AOK Bayern", "evidence": None})
    _, document = await run(world, FakeLanguageModel(responder(answer, extraction())))
    assert document.lane is Lane.YELLOW
    assert document.processing.status is ProcessingStatus.REVIEW
    assert document.contact_id is None  # only proposed
    assert document.document_type_id == seeded.types["Rechnung"].id  # verified, so applied
    assert "document.filed" not in world.event_types()
