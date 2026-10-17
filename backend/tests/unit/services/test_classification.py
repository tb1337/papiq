"""The language model's signal, the judge and the fields node with a fake language model:
every check, the answer handling, and the way through the pipeline."""

import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest

from papiq.adapters.outbound.memory import FakeLanguageModel, FakeTextLocator
from papiq.adapters.outbound.memory.processing import FAKE_PDF
from papiq.adapters.outbound.regex import RegexPatternMatcher
from papiq.core.domain.classification import TITLE, FieldCheck, checks_from_json, field_key
from papiq.core.domain.documents import UNSET, Document, DocumentChanges
from papiq.core.domain.errors import LanguageModelError
from papiq.core.domain.exchanges import RunExchange, exchanges_from_json
from papiq.core.domain.fields import FieldDefinition, FieldType, Money
from papiq.core.domain.identifiers import Identifier, IdentifierKind
from papiq.core.domain.json_value import JsonObject, JsonValue
from papiq.core.domain.master_data import Assignment, Contact, DocumentType, Tag, TagScope
from papiq.core.domain.pipeline import Lane, Node, NodeResult, NodeRun, Outcome, ProcessingStatus
from papiq.core.domain.reading import PageReading, readings_to_json
from papiq.core.domain.users import Role
from papiq.core.ports import StructuredRequest
from papiq.core.services.classification.nodes import (
    NO_MODEL,
    ClassificationPolicy,
    FieldsNode,
    JudgeNode,
    LlmSignal,
)
from papiq.core.services.classification.prompts import EXTRACT_SYSTEM, PROPOSE_SYSTEM
from papiq.core.services.classification.signals import (
    ABSTAINED,
    ClassifierSignal,
    FactsResult,
    FactsSignal,
    NeighboursSignal,
)
from papiq.core.services.objects import archive_key, exchanges_key, text_key
from papiq.core.services.pipeline import MetadataResult, NodeExecutor, PlaceholderNode
from papiq.core.services.rules.nodes import ApplyRulesNode
from tests import builders
from tests.builders import NOW, incoming
from tests.unit.services.conftest import World

INVOICE = """\
# Stadtwerke Musterstadt GmbH
Postfach 12 34, 12345 Musterstadt

Herrn Max Nord, Musterweg 1, 12345 Musterstadt

Musterstadt, 31.03.2026

## Rechnung Nr. R-2026-0815

Stromverbrauch vom 01.01.2026 bis 31.03.2026

| Position | Betrag |
| --- | --- |
| Arbeitspreis | 70,76 € |
| Gesamtbetrag | 84,20 € |

Zahlbar bis 15.04.2026.
"""
IBAN = "DE89 3704 0044 0532 0130 00"


@dataclass
class Seeded:
    contacts: dict[str, Contact]
    types: dict[str, DocumentType]
    tags: dict[str, Tag]
    fields: dict[str, FieldDefinition]


async def seed(world: World, contacts: tuple[str, ...] = ()) -> Seeded:
    names = contacts or ("Stadtwerke Musterstadt GmbH", "Stadtwerke Beispielstadt", "AOK Bayern")
    seeded = Seeded(
        contacts={name: Contact.create(name=name, now=NOW) for name in names},
        types={name: DocumentType.create(name=name, now=NOW) for name in ("Rechnung", "Vertrag")},
        tags={name: Tag.create(name=name, now=NOW) for name in ("Strom", "Versicherung")},
        fields={},
    )
    invoice = seeded.types["Rechnung"].id
    for name, data_type, scope in [
        ("Rechnungsbetrag", FieldType.AMOUNT, [invoice]),
        ("Rechnungsnummer", FieldType.TEXT, [invoice]),
        ("Fällig am", FieldType.DATE, [invoice]),
        ("Bezahlt", FieldType.BOOLEAN, None),
    ]:
        # Bound to the invoice, the fields are required for it; the global one is optional.
        seeded.fields[name] = FieldDefinition.create(
            name=name,
            data_type=data_type,
            document_type_ids=scope,
            required_types=scope or (),
            now=NOW,
        )
    async with world.uow() as uow:
        for contact in seeded.contacts.values():
            await uow.contacts.add(contact)
        for kind in seeded.types.values():
            await uow.document_types.add(kind)
        for tag in seeded.tags.values():
            await uow.tags.add(tag)
        for field in seeded.fields.values():
            await uow.fields.add(field)
        await uow.commit()
    return seeded


FIELDS: list[JsonValue] = [
    # Keys by name: a1 Bezahlt, a2 Fällig am, a3 Rechnungsbetrag, a4 Rechnungsnummer.
    {"key": "a2", "value": "15.04.2026", "evidence": "Zahlbar bis 15.04.2026."},
    {"key": "a3", "value": "84,20 €", "evidence": "Gesamtbetrag | 84,20 €"},
    {"key": "a4", "value": "R-2026-0815", "evidence": "Rechnung Nr. R-2026-0815"},
]


def proposal(**changes: Any) -> JsonObject:
    """The language model's answer to the signal's request."""
    answer: JsonObject = {
        "sender": {"value": "Stadtwerke Musterstadt GmbH", "evidence": None},
        "recipient": "Max Nord",
        "document_type": "Rechnung",
        "new_document_type": None,
        "topic": None,
        "tags": ["Strom"],
        "new_tags": [],
        "document_date": {"value": "2026-03-31", "evidence": "Musterstadt, 31.03.2026"},
        "title": "Stromrechnung März 2026",
        "language": "de",
        "fields": FIELDS,
    }
    return answer | changes


def extraction(**values: Any) -> JsonObject:
    """Answer of the fields node's own request, for the fields in key order: Bezahlt, Fällig
    am, Rechnungsbetrag, Rechnungsnummer (sorted by name)."""
    defaults: dict[str, Any] = {
        "a1": {"value": None, "evidence": None},
        "a2": {"value": "2026-04-15", "evidence": "Zahlbar bis 15.04.2026."},
        "a3": {
            "value": {"amount": "84.20", "currency": "EUR"},
            "evidence": "Gesamtbetrag | 84,20 €",
        },
        "a4": {"value": "R-2026-0815", "evidence": "Rechnung Nr. R-2026-0815"},
    }
    return {"fields": defaults | values}


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
    document = builders.document(owner, await world.default_space(owner))
    document.document_type_id = None if type is None else type.id
    async with world.uow() as uow:
        await uow.documents.add(document)
        await uow.commit()
    await world.object_store.put(
        text_key(document.id), text.encode(), content_type="text/markdown; charset=utf-8"
    )
    return document


def rules_step(world: World) -> ApplyRulesNode:
    return ApplyRulesNode(
        world.uow,
        world.object_store,
        RegexPatternMatcher(0.2),
        builders.TEMPLATES,
        max_text=200_000,
    )


def signal_step(world: World, fake: FakeLanguageModel | None, **policy: Any) -> LlmSignal:
    return LlmSignal(
        world.uow, world.object_store, fake, world.clock, ClassificationPolicy(**policy)
    )


def fields_step(world: World, fake: FakeLanguageModel | None) -> FieldsNode:
    return FieldsNode(world.uow, world.object_store, fake, world.clock)


async def record(world: World, document: Document, node: Node, result: NodeResult) -> None:
    async with world.uow() as uow:
        await uow.processing_log.append(
            NodeRun(
                document_id=document.id,
                node=node,
                run=document.processing.run,
                result=result,
                pipeline_version="test",
                started_at=NOW,
                duration=timedelta(0),
            )
        )
        await uow.commit()


async def judge(
    world: World,
    document: Document,
    fake: FakeLanguageModel | None,
    *,
    locator: FakeTextLocator | None = None,
    **policy: Any,
) -> NodeResult | MetadataResult:
    """The model's signal and the facts as the pipeline logs them, then the judge; the
    signal's result if it fails."""
    signals: list[tuple[Node, LlmSignal | FactsSignal]] = [
        (Node.SIGNAL_LLM, signal_step(world, fake, **policy)),
        (Node.SIGNAL_FACTS, FactsSignal(world.uow, world.object_store)),
    ]
    for node, signal in signals:
        ran = await signal.run(document)
        result = ran.result if isinstance(ran, FactsResult) else ran
        if result.outcome is Outcome.FAILED:
            return result
        await record(world, document, node, result)
    step = JudgeNode(
        world.uow, world.object_store, world.clock, ClassificationPolicy(**policy), locator=locator
    )
    return await step.run(document)


def checks(result: NodeResult) -> dict[str, FieldCheck]:
    return {check.field: check for check in checks_from_json(result.output["fields"])}


async def classify(world: World, answer: JsonObject, text: str = INVOICE) -> MetadataResult:
    document = await document_with(world, text)
    result = await judge(world, document, model(answer))
    assert isinstance(result, MetadataResult)
    return result


def output(result: NodeResult) -> Any:
    return result.output


# --- the language model's signal -------------------------------------------------------------


async def test_the_signal_asks_once_and_keeps_the_proposal(world: World) -> None:
    seeded = await seed(world)
    fake = model(proposal())
    document = await document_with(world)

    result = await signal_step(world, fake).run(document)

    assert result.outcome is Outcome.OK
    assert result.model_version == "fake-llm 1"
    assert result.input["truncated"] is False
    assert (result.input["model"], result.input["endpoint"]) == ("fake-llm 1", "memory")
    assert result.input["contacts"] == 3
    fields = seeded.fields
    assert result.input["fields"] == {
        "a1": str(fields["Bezahlt"].id),
        "a2": str(fields["Fällig am"].id),
        "a3": str(fields["Rechnungsbetrag"].id),
        "a4": str(fields["Rechnungsnummer"].id),
    }
    assert output(result)["attempts"] == 1
    # Per attempt; the fake model reports no usage.
    assert output(result)["tokens"] == [{"prompt": None, "completion": None, "usage": {}}]
    assert output(result)["answer"] == proposal()
    kept = output(result)["proposal"]
    assert kept["sender"] == {"value": "Stadtwerke Musterstadt GmbH", "evidence": None}
    assert (kept["recipient"], kept["title"], kept["language"]) == (
        "Max Nord",
        "Stromrechnung März 2026",
        "de",
    )
    # Fields by id, so that a renamed field or a new key order does not mix them up.
    assert kept["fields"][str(fields["Rechnungsbetrag"].id)] == {
        "value": "84,20 €",
        "evidence": "Gesamtbetrag | 84,20 €",
    }
    assert str(fields["Bezahlt"].id) not in kept["fields"]

    (request,) = fake.requests
    assert request.schema_name == "proposal"
    assert request.system == PROPOSE_SYSTEM
    assert (
        "Document types, each with its fields:\n- Rechnung\n"
        '  - a2: "Fällig am" (date)\n'
        '  - a3: "Rechnungsbetrag" (amount with currency)\n'
        '  - a4: "Rechnungsnummer" (text)\n'
        "- Vertrag\n\n"
        'Fields for every document:\n- a1: "Bezahlt" (yes or no)\n\n'
    ) in request.user
    assert "- Strom\n- Versicherung" in request.user
    # Contacts the text names (half of "Stadtwerke Beispielstadt"), not "AOK Bayern".
    assert "Contacts:\n- Stadtwerke Musterstadt GmbH\n- Stadtwerke Beispielstadt\n" in request.user
    assert "Postfach 12 34" in request.user


async def test_without_a_model_the_signal_abstains(world: World) -> None:
    result = await signal_step(world, None).run(await document_with(world))
    assert (result.outcome, output(result)[ABSTAINED]) == (Outcome.OK, NO_MODEL)


async def test_an_unfit_answer_is_asked_again(world: World) -> None:
    await seed(world)
    fake = model("Sure! Here is the JSON.", proposal())
    result = await signal_step(world, fake).run(await document_with(world))
    assert result.outcome is Outcome.OK
    assert output(result)["attempts"] == 2
    first, second = fake.requests
    assert second.user.startswith(first.user)
    assert "Your previous answer was not valid: the answer is not JSON" in second.user


async def test_two_unfit_answers_fail_the_signal(world: World) -> None:
    await seed(world)
    injected = proposal() | {"space": "Shared", "owner": "mallory"}
    result = await signal_step(world, model(injected)).run(await document_with(world))
    assert result.outcome is Outcome.FAILED
    assert result.reason == (
        "the model's answer does not fit the schema: the answer: unexpected owner, space"
    )
    assert output(result)["attempts"] == 2
    assert len(output(result)["answers"]) == 2


async def test_an_unreachable_model_raises_for_a_retry(world: World) -> None:
    def fail(request: StructuredRequest) -> str:
        raise LanguageModelError("cannot reach ollama:11434")

    with pytest.raises(LanguageModelError):
        await signal_step(world, FakeLanguageModel(fail)).run(await document_with(world))


async def exchanges(world: World, document: Document, node: Node) -> list[RunExchange]:
    return exchanges_from_json(
        json.loads(await world.object_store.get(exchanges_key(document.id, node)))
    )


async def test_what_was_sent_is_kept_per_run(world: World) -> None:
    await seed(world)
    fake = model("Sure! Here is the JSON.", proposal())
    document = await document_with(world)
    await signal_step(world, fake).run(document)

    (kept,) = await exchanges(world, document, Node.SIGNAL_LLM)
    assert kept.run == document.processing.run
    first, second = kept.calls
    assert (first.system, first.user) == (PROPOSE_SYSTEM, fake.requests[0].user)
    assert first.answer == "Sure! Here is the JSON."
    assert second.user == fake.requests[1].user
    assert json.loads(second.answer) == proposal()
    assert (second.model, second.usage) == ("fake-llm 1", {})

    # The same run again replaces its calls; a later run is added.
    await signal_step(world, model(proposal())).run(document)
    (again,) = await exchanges(world, document, Node.SIGNAL_LLM)
    assert len(again.calls) == 1
    later = replace(document, processing=replace(document.processing, run=2))
    await signal_step(world, model(proposal())).run(later)
    assert [run.run for run in await exchanges(world, document, Node.SIGNAL_LLM)] == [1, 2]


async def test_the_exchanges_of_a_failed_signal_are_kept(world: World) -> None:
    await seed(world)
    document = await document_with(world)
    await signal_step(world, model("not json")).run(document)
    (kept,) = await exchanges(world, document, Node.SIGNAL_LLM)
    assert [call.answer for call in kept.calls] == ["not json", "not json"]


async def test_a_failure_to_keep_the_exchanges_does_not_fail_the_signal(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    await seed(world)
    document = await document_with(world)
    put = world.object_store.put

    async def failing(key: str, data: bytes, *, content_type: str) -> None:
        if "/exchanges/" in key:
            raise OSError("the object store is full")
        await put(key, data, content_type=content_type)

    monkeypatch.setattr(world.object_store, "put", failing)
    result = await signal_step(world, model(proposal())).run(document)
    assert result.outcome is Outcome.OK
    assert not await world.object_store.exists(exchanges_key(document.id, Node.SIGNAL_LLM))


async def test_long_texts_are_shortened_but_checked_in_full(world: World) -> None:
    await seed(world)
    text = (
        INVOICE.replace("Musterstadt, 31.03.2026\n", "")
        + "\n".join(f"Zeile {number}" for number in range(3000))
        + "\nMusterstadt, 31.03.2026\n"
    )
    fake = model(proposal())
    outcome = await judge(world, await document_with(world, text), fake, input_budget=2000)
    assert isinstance(outcome, MetadataResult)
    assert outcome.result.input["truncated"] is True
    assert "characters left out" in fake.requests[0].user
    assert len(fake.requests[0].user) < 3000
    assert checks(outcome.result)["document_date"].ok


async def test_many_tags_list_those_named_in_the_text_first(world: World) -> None:
    await seed(world)
    async with world.uow() as uow:
        for number in range(10):
            await uow.tags.add(Tag.create(name=f"Aaa {number}", now=NOW))
        await uow.commit()
    fake = model(proposal())
    text = INVOICE + "\nTarif: Strom Basis\n"
    await signal_step(world, fake, max_tags=3).run(await document_with(world, text))
    request = fake.requests[0]
    assert "- Strom" in request.user
    assert request.user.count("- Aaa") == 2


async def add_contact(world: World, name: str, *aliases: str) -> Contact:
    contact = Contact.create(name=name, now=NOW, aliases=aliases)
    async with world.uow() as uow:
        await uow.contacts.add(contact)
        await uow.commit()
    return contact


EXAMPLE_LETTER = INVOICE.replace("Stadtwerke Musterstadt GmbH", "Nord Krankenversicherung AG")


async def test_contacts_the_text_names_most_are_listed(world: World) -> None:
    await seed(world)
    await add_contact(world, "Nord Versicherungsgruppe", "Nord Krankenversicherung AG")
    await add_contact(world, "Südstädter Versicherung")
    await add_contact(world, "Nord Sport")
    fake = model(proposal())
    text = EXAMPLE_LETTER + "\nINTER, Südstadt\n"
    result = await signal_step(world, fake, max_contacts=3).run(await document_with(world, text))
    contacts = fake.requests[0].user.split("\n\nDocument types")[0]
    assert contacts == (
        "Contacts:\n"
        "- Nord Versicherungsgruppe (also written as: Nord Krankenversicherung AG)\n"
        "- Nord Sport\n"  # half of the words, like the next; then by name
        "- Stadtwerke Musterstadt GmbH"
    )  # not the contacts whose words the text does not show
    assert result.input["contacts_listed"] == 3


async def test_type_descriptions_are_listed(world: World) -> None:
    await seed(world)
    async with world.uow() as uow:
        await uow.document_types.add(
            DocumentType.create(name="Lohnabrechnung", now=NOW, description="Entgeltbescheinigung")
        )
        await uow.commit()
    fake = model(proposal())
    await signal_step(world, fake).run(await document_with(world))
    assert "- Lohnabrechnung: Entgeltbescheinigung\n- Rechnung\n" in fake.requests[0].user


# --- the judge --------------------------------------------------------------------------------


async def test_a_verified_classification_is_applied(world: World) -> None:
    seeded = await seed(world)
    outcome = await judge(world, await document_with(world), model(proposal()))

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
    assert result.input == {
        "truncated": False,
        "model": "fake-llm 1",
        "endpoint": "memory",
        "signals": {"signal_facts": "facts", "signal_llm": "fake-llm 1"},
    }
    found = checks(result)
    assert set(found) == {"contact", "document_type", "topic", "tags", "document_date"}
    (candidate, *_) = found["contact"].candidates
    assert [source.signal for source in candidate.sources] == ["signal_llm", "signal_facts"]
    assert candidate.sources[1].named_in_text is True


@pytest.mark.parametrize(
    "name",
    ["Stadtwerke Musterstadt", "STADTWERKE MUSTERSTADT GMBH", "Stadtwerke Musterstdt GmbH"],
)
async def test_the_contact_is_matched_and_shown_in_the_text(world: World, name: str) -> None:
    seeded = await seed(world)
    result = await classify(world, proposal(sender={"value": name, "evidence": None}))
    contact = checks(result.result)["contact"]
    assert contact.ok
    assert contact.confidence >= 0.9
    assert result.changes.contact_id == seeded.contacts["Stadtwerke Musterstadt GmbH"].id


async def test_an_unknown_contact_is_proposed_as_new(world: World) -> None:
    await seed(world)
    text = INVOICE.replace("Stadtwerke Musterstadt GmbH", "Elektro Huber")
    result = await classify(
        world, proposal(sender={"value": "Elektro Huber", "evidence": None}), text
    )
    contact = checks(result.result)["contact"]
    assert contact.outcome is Outcome.UNCERTAIN
    assert contact.new_name == "Elektro Huber"
    assert contact.suggestion is None
    assert result.changes.contact_id is UNSET
    assert result.result.outcome is Outcome.UNCERTAIN
    assert (result.result.reason or "").startswith(
        "contact: no contact matches 'Elektro Huber'; a new contact is proposed"
    )


async def test_a_contact_absent_from_the_text_is_only_suggested(world: World) -> None:
    seeded = await seed(world)
    result = await classify(world, proposal(sender={"value": "AOK Bayern", "evidence": None}))
    contact = checks(result.result)["contact"]
    assert contact.outcome is Outcome.UNCERTAIN
    assert contact.reason == "the contact 'AOK Bayern' does not appear in the text"
    assert contact.suggestion == str(seeded.contacts["AOK Bayern"].id)
    assert contact.confidence < 0.9


async def test_a_similar_contact_is_suggested(world: World) -> None:
    seeded = await seed(world)
    result = await classify(
        world, proposal(sender={"value": "Stadtwerk Musterstadt Netz", "evidence": None})
    )
    contact = checks(result.result)["contact"]
    assert contact.outcome is Outcome.UNCERTAIN
    assert contact.suggestion == str(seeded.contacts["Stadtwerke Musterstadt GmbH"].id)
    assert contact.reason == (
        "'Stadtwerk Musterstadt Netz' is only similar to the contact 'Stadtwerke Musterstadt GmbH'"
    )
    assert 0.75 <= contact.confidence < 0.9


async def test_the_contact_itself_must_be_named(world: World) -> None:
    """A typo in the text matches the contact closely, but the contact is not named."""
    seeded = await seed(world)
    text = INVOICE.replace("Stadtwerke Musterstadt GmbH", "Stadtwerke Musterstad GmbH")
    result = await classify(
        world, proposal(sender={"value": "Stadtwerke Musterstad GmbH", "evidence": None}), text
    )
    contact = checks(result.result)["contact"]
    assert contact.outcome is Outcome.UNCERTAIN
    assert contact.suggestion == str(seeded.contacts["Stadtwerke Musterstadt GmbH"].id)
    assert contact.reason == "the contact 'Stadtwerke Musterstadt GmbH' does not appear in the text"


async def test_ambiguous_contacts_are_uncertain(world: World) -> None:
    await seed(world, contacts=("Muster GmbH", "Muster AG"))
    text = INVOICE.replace("Stadtwerke Musterstadt GmbH", "Muster")
    result = await classify(world, proposal(sender={"value": "Muster", "evidence": None}), text)
    contact = checks(result.result)["contact"]
    assert contact.outcome is Outcome.UNCERTAIN
    assert contact.reason == "'Muster' matches several contacts"


async def test_the_recipient_is_never_the_contact(world: World) -> None:
    seeded = await seed(world)
    await add_contact(world, "Max Nord")
    result = await classify(world, proposal(sender={"value": "Max Nord", "evidence": None}))
    contact = checks(result.result)["contact"]
    assert contact.outcome is Outcome.UNCERTAIN
    assert contact.reason == "'Max Nord' is the recipient, not the sender"
    assert result.changes.contact_id is UNSET
    assert seeded.contacts  # the others stay untouched


async def test_an_identifier_in_the_text_backs_the_contact(world: World) -> None:
    """The model reads only a similar name; the contact's IBAN in the text stands for it,
    but alone it is no more than a suggestion."""
    seeded = await seed(world)
    utility = seeded.contacts["Stadtwerke Musterstadt GmbH"]
    utility.set_identifiers([Identifier.parse(IdentifierKind.IBAN, IBAN)])
    async with world.uow() as uow:
        await uow.contacts.update(utility)
        await uow.commit()
    text = INVOICE.replace("Stadtwerke Musterstadt GmbH", "SWM") + f"\nIBAN {IBAN}\n"
    result = await classify(world, proposal(sender={"value": "SWM", "evidence": None}), text)
    contact = checks(result.result)["contact"]
    assert contact.outcome is Outcome.UNCERTAIN
    assert contact.suggestion == str(utility.id)
    assert contact.reason == "'SWM' is no contact, and only other signals point to " + (
        "'Stadtwerke Musterstadt GmbH'"
    )
    (candidate, new) = contact.candidates
    assert candidate.sources[0].identifiers == ("iban",)
    assert (new.value, new.label) == (None, "SWM")


async def test_no_contact(world: World) -> None:
    await seed(world)
    result = await classify(world, proposal(sender={"value": None, "evidence": None}))
    assert checks(result.result)["contact"].reason == "no contact recognised"


@pytest.mark.parametrize(
    ("answer", "reason", "new_name"),
    [
        (
            {"document_type": "Mahnung"},
            "no document type fits; a new type 'Mahnung' is proposed",
            "Mahnung",
        ),
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
    result = await classify(world, proposal(**answer))
    kind = checks(result.result)["document_type"]
    assert kind.outcome is Outcome.UNCERTAIN
    assert kind.reason == reason
    assert kind.new_name == new_name
    assert result.changes.document_type_id is UNSET


async def test_document_types_are_matched_regardless_of_case(world: World) -> None:
    seeded = await seed(world)
    result = await classify(world, proposal(document_type="rechnung"))
    assert result.changes.document_type_id == seeded.types["Rechnung"].id


async def test_new_tags_are_only_proposed(world: World) -> None:
    seeded = await seed(world)
    result = await classify(
        world, proposal(tags=["Strom", "Energie", "Strom"], new_tags=["Abschlag"])
    )
    tags = checks(result.result)["tags"]
    assert tags.ok
    assert tags.value == [str(seeded.tags["Strom"].id)]
    assert tags.suggestion is None  # the new ones stand in the proposal
    assert tags.proposed == {"tags": ["Strom", "Energie", "Strom"], "new_tags": ["Abschlag"]}
    assert result.result.outcome is Outcome.OK
    assert result.add_tags == frozenset({seeded.tags["Strom"].id})


async def test_tags_set_by_hand_are_neither_listed_nor_set(world: World) -> None:
    seeded = await seed(world)
    paid = Tag.create(name="Bezahlt", now=NOW, assignment=Assignment.MANUAL)
    group = Tag.create(name="Versorger", now=NOW, scope=TagScope.CONTACT)
    async with world.uow() as uow:
        await uow.tags.add(paid)
        await uow.tags.add(group)
        await uow.commit()
    fake = model(proposal(tags=["Strom", "Bezahlt", "Versorger"]))
    result = await judge(world, await document_with(world, INVOICE), fake)
    assert "- Bezahlt" not in fake.requests[0].user and "- Versorger" not in fake.requests[0].user
    assert "- Strom" in fake.requests[0].user
    assert isinstance(result, MetadataResult)
    assert result.add_tags == frozenset({seeded.tags["Strom"].id})


@pytest.mark.parametrize(
    ("value", "evidence", "reason", "suggestion"),
    [
        ("2026-03-31", None, "no quoted passage", "2026-03-31"),
        (
            "2026-03-30",
            "Musterstadt, 31.03.2026",
            "the quoted passage does not show the value",
            "2026-03-30",
        ),
        (
            "2026-03-31",
            "31.03.2025",
            "the quoted passage does not appear in the text",
            "2026-03-31",
        ),
        ("31.13.2026", None, "'31.13.2026' is not a date", None),
        ("2026-02-30", None, "'2026-02-30' is not a date", None),
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
        world, proposal(document_date={"value": value, "evidence": evidence}), text
    )
    check = checks(result.result)["document_date"]
    assert check.outcome is Outcome.UNCERTAIN
    assert check.reason == reason
    assert check.suggestion == suggestion
    assert result.changes.document_date is UNSET


async def test_a_date_written_in_german_is_read(world: World) -> None:
    await seed(world)
    answer = proposal(document_date={"value": "31. März 2026", "evidence": "Stand: 31. März 2026"})
    result = await classify(world, answer, INVOICE + "\nStand: 31. März 2026\n")
    assert result.changes.document_date == date(2026, 3, 31)


async def test_nothing_recognised_fails(world: World) -> None:
    await seed(world)
    result = await judge(world, await document_with(world), FakeLanguageModel())
    assert isinstance(result, NodeResult)
    assert result.outcome is Outcome.FAILED
    assert result.reason == "nothing recognised: no contact, document type or date"


async def test_without_a_model_a_person_classifies(world: World) -> None:
    await seed(world)
    outcome = await judge(world, await document_with(world), None)
    assert isinstance(outcome, MetadataResult)
    assert outcome.result.outcome is Outcome.UNCERTAIN
    assert outcome.result.model_version == "judge"
    assert outcome.result.input == {"signals": {"signal_facts": "facts", "signal_llm": "abstained"}}
    found = checks(outcome.result)
    assert found["contact"].reason == "no contact recognised (the language model did not answer)"
    assert found["document_date"].reason == (
        f"no date recognised: the language model gave none ({NO_MODEL})"
    )
    assert outcome.changes == DocumentChanges()


async def test_a_contact_named_by_an_alias_is_applied(world: World) -> None:
    """The model gives the listed name or the one in the text; the text shows an alias."""
    await seed(world)
    example = await add_contact(world, "Nord Versicherungsgruppe", "Nord Krankenversicherung AG")
    for name in ("Nord Versicherungsgruppe", "Nord Krankenversicherung AG"):
        result = await classify(
            world, proposal(sender={"value": name, "evidence": None}), EXAMPLE_LETTER
        )
        assert checks(result.result)["contact"].ok
        assert result.changes.contact_id == example.id


async def test_without_the_alias_the_contact_is_new(world: World) -> None:
    await seed(world)
    await add_contact(world, "Nord Versicherungsgruppe")
    result = await classify(
        world,
        proposal(sender={"value": "Nord Krankenversicherung AG", "evidence": None}),
        EXAMPLE_LETTER,
    )
    contact = checks(result.result)["contact"]
    assert contact.new_name == "Nord Krankenversicherung AG"


async def test_an_alias_of_another_contact_does_not_count_as_named(world: World) -> None:
    """The other insurer's alias in the text does not name the example."""
    await seed(world)
    example = await add_contact(world, "Nord Versicherungsgruppe")
    await add_contact(world, "Muster", "Nord Krankenversicherung AG Muster")
    result = await classify(
        world,
        proposal(sender={"value": "Nord Versicherungsgruppe", "evidence": None}),
        EXAMPLE_LETTER,
    )
    contact = checks(result.result)["contact"]
    assert not contact.ok
    assert contact.suggestion == str(example.id)


# --- fields -------------------------------------------------------------------------------


async def extract(
    world: World, answer: JsonObject, text: str = INVOICE
) -> tuple[Seeded, MetadataResult]:
    seeded = await seed(world)
    document = await document_with(world, text, seeded.types["Rechnung"])
    result = await fields_step(world, model(answer)).run(document)
    assert isinstance(result, MetadataResult)
    return seeded, result


def field_check(seeded: Seeded, result: MetadataResult, name: str) -> FieldCheck:
    return checks(result.result)[field_key(seeded.fields[name].id)]


async def test_verified_fields_are_applied(world: World) -> None:
    seeded, result = await extract(world, extraction())
    assert result.result.outcome is Outcome.OK
    fields = seeded.fields
    assert result.changes.fields == {
        fields["Rechnungsbetrag"].id: Money(Decimal("84.20"), "EUR"),
        fields["Rechnungsnummer"].id: "R-2026-0815",
        fields["Fällig am"].id: date(2026, 4, 15),
    }
    paid = field_check(seeded, result, "Bezahlt")
    assert paid.ok and paid.value is None  # a global field may be missing
    assert result.result.input["fields"] == {
        "a1": str(fields["Bezahlt"].id),
        "a2": str(fields["Fällig am"].id),
        "a3": str(fields["Rechnungsbetrag"].id),
        "a4": str(fields["Rechnungsnummer"].id),
    }


@pytest.mark.parametrize(
    ("answer", "name", "reason", "suggestion"),
    [
        (
            {
                "a3": {
                    "value": {"amount": "99.99", "currency": "EUR"},
                    "evidence": "Gesamtbetrag | 84,20 €",
                }
            },
            "Rechnungsbetrag",
            "'Rechnungsbetrag': the quoted passage does not show the value",
            {"amount": "99.99", "currency": "EUR"},
        ),
        (
            {"a3": {"value": {"amount": "84.20", "currency": "EUR"}, "evidence": None}},
            "Rechnungsbetrag",
            "'Rechnungsbetrag': no quoted passage",
            {"amount": "84.20", "currency": "EUR"},
        ),
        (
            {
                "a3": {
                    "value": {"amount": "84.20", "currency": "USD"},
                    "evidence": "Gesamtbetrag | 84,20 €",
                }
            },
            "Rechnungsbetrag",
            "'Rechnungsbetrag': the currency USD does not appear in the text",
            {"amount": "84.20", "currency": "USD"},
        ),
        (
            {"a3": {"value": "zwölf Euro", "evidence": None}},
            "Rechnungsbetrag",
            "'zwölf Euro' is not a valid value of 'Rechnungsbetrag' (amount)",
            None,
        ),
        (
            {"a4": {"value": None, "evidence": None}},
            "Rechnungsnummer",
            "'Rechnungsnummer' was not found",
            None,
        ),
        (
            {"a4": {"value": "R-2026-0816", "evidence": "Rechnung Nr. R-2026-0815"}},
            "Rechnungsnummer",
            "'Rechnungsnummer': the quoted passage does not show the value",
            "R-2026-0816",
        ),
        (
            {"a4": {"value": "R-2026-0815", "evidence": "Rechnung Nr. R-2026-0815 vom"}},
            "Rechnungsnummer",
            "'Rechnungsnummer': the quoted passage does not appear in the text",
            "R-2026-0815",
        ),
        (
            {"a2": {"value": "2026-04-16", "evidence": "Zahlbar bis 15.04.2026."}},
            "Fällig am",
            "'Fällig am': the quoted passage does not show the value",
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
            {"a4": {"value": "R-2026", "evidence": "Rechnung Nr. R-2026-0815"}},
            "Rechnungsnummer",
            "'Rechnungsnummer': the quoted passage does not show the value",
            "R-2026",
        ),
        (
            {
                "a3": {
                    "value": {"amount": "84.20", "currency": "BIS"},
                    "evidence": "Gesamtbetrag | 84,20 €",
                }
            },
            "Rechnungsbetrag",
            "'Rechnungsbetrag': the currency BIS does not appear in the text",
            {"amount": "84.20", "currency": "BIS"},
        ),
    ],
)
async def test_uncertain_fields(
    world: World, answer: dict[str, Any], name: str, reason: str, suggestion: object
) -> None:
    seeded, result = await extract(world, extraction(**answer))
    check = field_check(seeded, result, name)
    assert check.outcome is Outcome.UNCERTAIN
    assert check.reason == reason
    assert check.suggestion == suggestion
    assert seeded.fields[name].id not in result.changes.fields
    assert result.result.outcome is Outcome.UNCERTAIN


async def test_a_date_equal_to_the_document_date_is_only_suggested(world: World) -> None:
    """Models put the document date into a due date the text does not give."""
    seeded = await seed(world)
    document = await document_with(world, type=seeded.types["Rechnung"])
    document.document_date = date(2026, 3, 31)
    answer = extraction(a2={"value": "2026-03-31", "evidence": "Musterstadt, 31.03.2026"})
    result = await fields_step(world, model(answer)).run(document)
    assert isinstance(result, MetadataResult)
    check = field_check(seeded, result, "Fällig am")
    assert check.outcome is Outcome.UNCERTAIN
    assert check.reason == "'Fällig am': the same date as the document date"
    assert check.suggestion == "2026-03-31"
    assert seeded.fields["Fällig am"].id not in result.changes.fields


async def test_values_are_read_leniently(world: World) -> None:
    seeded, result = await extract(
        world,
        extraction(
            a1={"value": True, "evidence": "Zahlbar bis 15.04.2026."},
            a3={
                "value": {"amount": "84,2", "currency": "eur"},
                "evidence": "Gesamtbetrag | 84,20 €",
            },
        ),
    )
    fields = seeded.fields
    assert result.changes.fields[fields["Rechnungsbetrag"].id] == Money(Decimal("84.2"), "EUR")
    assert result.changes.fields[fields["Bezahlt"].id] is True


async def test_an_optional_field_may_stay_empty_but_a_value_must_be_shown(world: World) -> None:
    seeded = await seed(world)
    number = seeded.fields["Rechnungsnummer"]
    number.require(types=[])
    async with world.uow() as uow:
        await uow.fields.update(number)
        await uow.commit()
    document = await document_with(world, INVOICE, seeded.types["Rechnung"])
    for answer, outcome in (
        (extraction(), Outcome.OK),
        (extraction(a4={"value": "R-2026", "evidence": None}), Outcome.UNCERTAIN),
    ):
        result = await fields_step(world, model(answer)).run(document)
        assert isinstance(result, MetadataResult)
        assert field_check(seeded, result, "Rechnungsnummer").outcome is outcome


PAY_SLIP = """\
# Nordlicht Software AG

Entgeltabrechnung März 2026

| Bezüge | Betrag |
| --- | --- |
| Gehalt | 5.166,67 |
| Nettoverdienst | 2.987,45 |
"""


@pytest.mark.parametrize(
    ("value", "currency", "outcome"),
    [
        ("2.987,45", "EUR", Outcome.OK),  # as the slip prints it
        ("2.987,45 EUR", "EUR", Outcome.OK),
        ("2.987,45 USD", "EUR", Outcome.UNCERTAIN),
        ("2.987,45 EUR", "CHF", Outcome.UNCERTAIN),
    ],
)
async def test_an_amount_without_any_currency_in_the_text_is_in_the_default_one(
    world: World, value: str, currency: str, outcome: Outcome
) -> None:
    seeded = await seed(world)
    document = await document_with(world, PAY_SLIP, seeded.types["Rechnung"])
    answer = extraction(a3={"value": value, "evidence": "| Nettoverdienst | 2.987,45 |"})
    step = FieldsNode(
        world.uow,
        world.object_store,
        model(answer),
        world.clock,
        ClassificationPolicy(default_currency=currency),
    )
    result = await step.run(document)
    assert isinstance(result, MetadataResult)
    check = field_check(seeded, result, "Rechnungsbetrag")
    assert check.outcome is outcome, check.reason
    if outcome is Outcome.OK:
        assert result.changes.fields[seeded.fields["Rechnungsbetrag"].id] == Money(
            Decimal("2987.45"), currency
        )
    else:
        assert (check.reason or "").endswith("does not appear in the text")


BROKEN_SCAN = """\
Abrechnung der Brutto-Netto-Bezüge
Gesamt-Brutto

0020Ausbildungsvergiitung kfm.
620
Netto-Verdienst 310,00 EUR
"""


@pytest.mark.parametrize(
    ("value", "evidence", "reason"),
    [
        # The label alone, the value from a fragment ten lines below (a real false green).
        ("620,00 EUR", "Gesamt-Brutto", "the quoted passage does not show the value"),
        ("620,00 EUR", "620", "the quoted passage does not show the value"),
        ("620,00 EUR", None, "no quoted passage"),
        ("310,00 EUR", "Netto-Verdienst 310,00 EUR", None),
    ],
)
async def test_the_quoted_passage_must_show_the_value(
    world: World, value: str, evidence: str | None, reason: str | None
) -> None:
    seeded, result = await extract(
        world, extraction(a3={"value": value, "evidence": evidence}), BROKEN_SCAN
    )
    check = field_check(seeded, result, "Rechnungsbetrag")
    assert check.reason == (None if reason is None else f"'Rechnungsbetrag': {reason}")
    assert check.ok is (reason is None)


def recognised(*readings: PageReading) -> NodeResult:
    """The OCR's entry, with how well it read the pages."""
    return NodeResult(outcome=Outcome.OK, output={"readings": readings_to_json(readings)})


@pytest.mark.parametrize(
    ("readings", "located", "reason"),
    [
        # The quote's page: a broken scan, 10 % of its words unsure.
        ((PageReading(1, 224, 23),), 1, "page 1 was poorly read (10% of its words unsure)"),
        ((PageReading(1, 224, 23), PageReading(2, 400, 8)), 2, None),  # the quote on a good page
        ((PageReading(1, 224, 23), PageReading(2, 400, 8)), None, "page 1 was poorly read"),
        ((PageReading(1, 409, 8),), None, None),  # a good scan
        ((), None, None),  # text of its own, or recognised before readings
    ],
)
async def test_a_value_on_a_poorly_read_page_is_only_suggested(
    world: World, readings: tuple[PageReading, ...], located: int | None, reason: str | None
) -> None:
    seeded = await seed(world)
    document = await document_with(world, INVOICE, seeded.types["Rechnung"])
    await record(world, document, Node.OCR, recognised(*readings))
    await world.object_store.put(archive_key(document.id), FAKE_PDF, content_type="application/pdf")
    pages = [""] * 2
    if located is not None:
        pages[located - 1] = INVOICE
    step = FieldsNode(
        world.uow,
        world.object_store,
        model(extraction()),
        world.clock,
        locator=FakeTextLocator(pages),
    )
    result = await step.run(document)
    assert isinstance(result, MetadataResult)
    amount = field_check(seeded, result, "Rechnungsbetrag")
    paid = field_check(seeded, result, "Bezahlt")
    assert paid.ok  # yes or no is not read as written
    if reason is None:
        assert amount.ok
        return
    assert (amount.reason or "").startswith(f"'Rechnungsbetrag': {reason}")
    assert amount.suggestion == {"amount": "84.20", "currency": "EUR"}
    assert seeded.fields["Rechnungsbetrag"].id not in result.changes.fields


async def test_the_document_date_on_a_poorly_read_page_is_only_suggested(world: World) -> None:
    await seed(world)
    document = await document_with(world)
    await record(world, document, Node.OCR, recognised(PageReading(1, 224, 23)))
    result = await judge(world, document, model(proposal()), poor_reading=0.11)
    assert isinstance(result, MetadataResult)
    assert checks(result.result)["document_date"].ok  # 10 % stays below 0.11
    result = await judge(world, document, model(proposal()))
    assert isinstance(result, MetadataResult)
    check = checks(result.result)["document_date"]
    assert check.reason == "page 1 was poorly read (10% of its words unsure)"
    assert check.suggestion == "2026-03-31"
    assert result.changes.document_date is UNSET


async def test_values_are_read_as_written_in_german(world: World) -> None:
    seeded, result = await extract(
        world,
        extraction(
            a2={"value": "15.04.2026", "evidence": "Zahlbar bis 15.04.2026."},
            a3={"value": "84,20 €", "evidence": "Gesamtbetrag | 84,20 €"},
        ),
    )
    fields = seeded.fields
    assert result.changes.fields[fields["Rechnungsbetrag"].id] == Money(Decimal("84.20"), "EUR")
    assert result.changes.fields[fields["Fällig am"].id] == date(2026, 4, 15)


async def test_no_fields_no_question(world: World) -> None:
    await seed(world)
    document = await document_with(world)  # no type: only the global field applies
    async with world.uow() as uow:
        for field in await uow.fields.list_all():
            if field.is_global:
                await uow.fields.remove(field.id)
        await uow.commit()
    fake = model(extraction())
    for step in (fields_step(world, fake), fields_step(world, None)):
        outcome = await step.run(document)
        assert isinstance(outcome, MetadataResult)
        assert outcome.result.outcome is Outcome.OK
        assert outcome.changes == DocumentChanges()
    assert fake.requests == []


async def test_fields_without_a_model(world: World) -> None:
    """Nothing reads them: the required fields of the invoice are open, the optional global one
    stays empty; with only optional fields the node is OK."""
    seeded = await seed(world)
    document = await document_with(world, type=seeded.types["Rechnung"])
    outcome = await fields_step(world, None).run(document)
    assert isinstance(outcome, MetadataResult)
    assert outcome.result.outcome is Outcome.UNCERTAIN
    number = field_check(seeded, outcome, "Rechnungsnummer")
    assert number.reason == f"'Rechnungsnummer' was not read: {NO_MODEL}"
    assert field_check(seeded, outcome, "Bezahlt").ok
    assert outcome.changes.fields == {}

    untyped = await document_with(world)  # only the optional global field applies
    outcome = await fields_step(world, None).run(untyped)
    assert isinstance(outcome, MetadataResult)
    assert outcome.result.outcome is Outcome.OK


async def test_extraction_prompt_names_type_and_fields(world: World) -> None:
    seeded = await seed(world)
    fake = model(extraction())
    await fields_step(world, fake).run(await document_with(world, type=seeded.types["Rechnung"]))
    (request,) = fake.requests
    assert 'The document is of type "Rechnung".' in request.user
    assert '- a3: "Rechnungsbetrag" (object with amount' in request.user
    assert request.schema_name == "fields"


async def set_by_hand(world: World, field: FieldDefinition) -> None:
    async with world.uow() as uow:
        stored = await uow.fields.get(field.id)
        stored.assignment = Assignment.MANUAL
        await uow.fields.update(stored)
        await uow.commit()


async def test_a_field_set_by_hand_is_never_asked_for(world: World) -> None:
    """Issue #52: neither the signal nor the fields node asks the model for it, and the fields
    node leaves its value as it is (whether a required one has one is the rules node's check)."""
    seeded = await seed(world)
    number, paid = seeded.fields["Rechnungsnummer"], seeded.fields["Bezahlt"]
    await set_by_hand(world, number)
    await set_by_hand(world, paid)
    document = await document_with(world, type=seeded.types["Rechnung"])
    fake = model(proposal())
    await signal_step(world, fake).run(document)
    (request,) = fake.requests
    assert "Rechnungsnummer" not in request.user and "Bezahlt" not in request.user

    given = extraction()["fields"]
    assert isinstance(given, dict)
    answer: JsonObject = {"fields": {"a1": given["a2"], "a2": given["a3"]}}  # Fällig, Betrag
    fake = model(answer)
    outcome = await fields_step(world, fake).run(document)
    assert isinstance(outcome, MetadataResult)
    (request,) = fake.requests
    assert "Rechnungsnummer" not in request.user and "Bezahlt" not in request.user
    assert outcome.result.outcome is Outcome.OK
    check = field_check(seeded, outcome, "Rechnungsnummer")
    assert check.ok and check.reason == "'Rechnungsnummer' is set by hand"
    assert number.id not in outcome.changes.fields and paid.id not in outcome.changes.fields


async def signalled(
    world: World, answer: JsonObject, type: DocumentType | None
) -> tuple[Document, NodeResult]:
    """A document of `type` whose log holds the model's signal with `answer`."""
    document = await document_with(world, type=type)
    result = await signal_step(world, model(answer)).run(document)
    await record(world, document, Node.SIGNAL_LLM, result)
    return document, result


async def test_the_signal_s_values_are_taken_over_for_its_type(world: World) -> None:
    seeded = await seed(world)
    document, _ = await signalled(world, proposal(), seeded.types["Rechnung"])
    fake = model(extraction())
    outcome = await fields_step(world, fake).run(document)

    assert isinstance(outcome, MetadataResult)
    assert fake.requests == []
    assert outcome.result.outcome is Outcome.OK
    assert outcome.result.model_version == "fake-llm 1"
    assert outcome.result.input == {"taken_over": 4}
    fields = seeded.fields
    assert outcome.changes.fields == {
        fields["Rechnungsbetrag"].id: Money(Decimal("84.20"), "EUR"),
        fields["Rechnungsnummer"].id: "R-2026-0815",
        fields["Fällig am"].id: date(2026, 4, 15),
    }
    assert field_check(seeded, outcome, "Bezahlt").ok  # optional, and not given


async def test_after_a_type_correction_the_fields_are_asked_for(world: World) -> None:
    """The model chose another type than the judge (or a person) decided on: it gave the
    global fields only, the others are asked for."""
    seeded = await seed(world)
    answer = proposal(
        document_type="Vertrag",
        fields=[{"key": "a1", "value": "ja", "evidence": "Zahlbar bis 15.04.2026."}],
    )
    document, _ = await signalled(world, answer, seeded.types["Rechnung"])
    fake = model(
        {
            "fields": {
                "a1": {"value": "2026-04-15", "evidence": "Zahlbar bis 15.04.2026."},
                "a2": {
                    "value": {"amount": "84.20", "currency": "EUR"},
                    "evidence": "Gesamtbetrag | 84,20 €",
                },
                "a3": {"value": "R-2026-0815", "evidence": "Rechnung Nr. R-2026-0815"},
            }
        }
    )
    outcome = await fields_step(world, fake).run(document)

    assert isinstance(outcome, MetadataResult)
    (request,) = fake.requests
    assert '- a1: "Fällig am"' in request.user
    assert "Bezahlt" not in request.user
    assert outcome.result.input["taken_over"] == 1
    assert outcome.result.outcome is Outcome.OK
    assert outcome.changes.fields[seeded.fields["Bezahlt"].id] is True
    assert outcome.changes.fields[seeded.fields["Rechnungsbetrag"].id] == Money(
        Decimal("84.20"), "EUR"
    )
    (kept,) = await exchanges(world, document, Node.FIELDS)
    assert kept.calls[0].system == EXTRACT_SYSTEM


async def test_an_optional_field_the_model_misreads_is_uncertain(world: World) -> None:
    seeded = await seed(world)
    junk = {"key": "a1", "value": "vielleicht", "evidence": None}
    document, _ = await signalled(world, proposal(fields=[*FIELDS, junk]), seeded.types["Rechnung"])
    outcome = await fields_step(world, model(extraction())).run(document)
    assert isinstance(outcome, MetadataResult)
    paid = field_check(seeded, outcome, "Bezahlt")
    assert paid.outcome is Outcome.UNCERTAIN
    assert paid.reason == "'vielleicht' is not a valid value of 'Bezahlt' (boolean)"
    assert outcome.result.outcome is Outcome.UNCERTAIN


# --- the title --------------------------------------------------------------------------------


async def titled(world: World, document: Document) -> FieldCheck | None:
    outcome = await fields_step(world, model(extraction())).run(document)
    assert isinstance(outcome, MetadataResult)
    title = checks(outcome.result).get(TITLE)
    assert outcome.changes.title == (UNSET if title is None else title.value)
    return title


async def test_the_title_comes_from_the_model(world: World) -> None:
    """A type's title pattern is a rule now (`test_title_patterns`), not the fields node's."""
    seeded = await seed(world)
    document, _ = await signalled(world, proposal(), seeded.types["Rechnung"])
    assert document.title == "scan"  # the file name
    title = await titled(world, document)
    assert title is not None
    assert (title.value, title.outcome, title.reason) == (
        "Stromrechnung März 2026",
        Outcome.OK,
        None,
    )


async def test_a_title_of_its_own_stays_but_the_machine_s_is_replaced(world: World) -> None:
    seeded = await seed(world)
    document, _ = await signalled(world, proposal(), seeded.types["Rechnung"])
    document.title = "Meine Stromrechnung"
    assert await titled(world, document) is None

    # The title the fields node set before is the machine's: a later run replaces it.
    document.title = "scan"
    first = await fields_step(world, model(extraction())).run(document)
    assert isinstance(first, MetadataResult)
    await record(world, document, Node.FIELDS, first.result)
    document.title = "Stromrechnung März 2026"
    title = await titled(world, document)
    assert title is not None
    assert title.value == "Stromrechnung März 2026"


# --- through the pipeline ---------------------------------------------------------------------


def responder(
    propose: JsonObject, extract: JsonObject
) -> Callable[[StructuredRequest], JsonObject]:
    return lambda request: propose if request.schema_name == "proposal" else extract


async def run(world: World, fake: FakeLanguageModel) -> tuple[Any, Document]:
    owner = await world.user()
    executors: dict[Node, NodeExecutor] = {
        Node.SIGNAL_LLM: signal_step(world, fake),
        Node.SIGNAL_FACTS: FactsSignal(world.uow, world.object_store),
        Node.SIGNAL_CLASSIFIER: ClassifierSignal(world.object_store, None),
        Node.SIGNAL_NEIGHBOURS: NeighboursSignal(world.uow, world.object_store, None, None),
        Node.JUDGE: JudgeNode(world.uow, world.object_store, world.clock),
        Node.FIELDS: fields_step(world, fake),
    }
    pipeline = world.pipeline(executors | {Node.APPLY_RULES: rules_step(world)})
    document = await pipeline.receive(owner.id, incoming(b"%PDF-1.7\n%%EOF\n"), filename="a.pdf")
    await world.object_store.put(
        text_key(document.id), INVOICE.encode(), content_type="text/markdown; charset=utf-8"
    )
    await world.drain(pipeline)
    return owner, await world.documents.get(owner.id, document.id)


async def test_a_verified_document_is_filed_green(world: World) -> None:
    seeded = await seed(world)
    fake = FakeLanguageModel(responder(proposal(), extraction()))
    owner, document = await run(world, fake)
    assert document.lane is Lane.GREEN
    assert document.contact_id == seeded.contacts["Stadtwerke Musterstadt GmbH"].id
    assert document.document_type_id == seeded.types["Rechnung"].id
    assert document.tag_ids == {seeded.tags["Strom"].id}
    assert document.document_date == date(2026, 3, 31)
    assert document.fields[seeded.fields["Rechnungsbetrag"].id] == Money(Decimal("84.20"), "EUR")
    assert document.title == "Stromrechnung März 2026"
    assert "document.filed" in world.event_types()
    assert len(fake.requests) == 1  # one request for the whole document
    log = await world.documents.processing_log(owner.id, document.id)
    proposed = next(entry for entry in log if entry.node is Node.SIGNAL_LLM)
    assert proposed.result.output["answer"] == proposal()
    judged = next(entry for entry in log if entry.node is Node.JUDGE)
    assert len(checks(judged.result)) == 5
    assert judged.result.input["signals"] == {
        "signal_classifier": "abstained",
        "signal_facts": "facts",
        "signal_llm": "fake-llm 1",
        "signal_neighbours": "abstained",
    }


async def test_an_uncertain_document_waits_in_the_inbox(world: World) -> None:
    seeded = await seed(world)
    answer = proposal(sender={"value": "AOK Bayern", "evidence": None})
    _, document = await run(world, FakeLanguageModel(responder(answer, extraction())))
    assert document.lane is Lane.YELLOW
    assert document.processing.status is ProcessingStatus.REVIEW
    assert document.contact_id is None  # only proposed
    assert document.document_type_id == seeded.types["Rechnung"].id  # verified, so applied
    assert "document.filed" not in world.event_types()


async def test_confirming_renders_the_title_pattern_with_the_decided_values(
    world: World,
) -> None:
    """The invoice number is missing: the model's title stands until the owner enters it; the
    pattern's rule runs again with the decided values."""
    seeded = await seed(world)
    admin = await world.user(role=Role.ADMIN)
    pattern = "{{ document_type }} {{ field.rechnungsnummer }}"
    await world.master_data.change_document_type(
        admin.id, seeded.types["Rechnung"].id, title_pattern=pattern
    )
    answer = proposal(fields=FIELDS[:2])
    owner, document = await run(world, FakeLanguageModel(responder(answer, extraction())))
    assert document.lane is Lane.YELLOW
    assert document.title == "Stromrechnung März 2026"

    number = seeded.fields["Rechnungsnummer"].id
    pipeline = world.pipeline({Node.APPLY_RULES: rules_step(world), Node.FILE: PlaceholderNode()})
    await pipeline.confirm(owner.id, document.id, DocumentChanges(fields={number: "R-2026-0815"}))
    await world.drain(pipeline)
    document = await world.documents.get(owner.id, document.id)
    assert document.title == "Rechnung R-2026-0815"


# --- quotes on the page ---------------------------------------------------------------------


async def test_quotes_are_located_in_the_archive(world: World) -> None:
    await seed(world)
    document = await document_with(world)
    await world.object_store.put(archive_key(document.id), FAKE_PDF, content_type="application/pdf")
    locator = FakeTextLocator([INVOICE])
    outcome = await judge(world, document, model(proposal()), locator=locator)
    assert isinstance(outcome, MetadataResult)
    found = checks(outcome.result)
    dated = found["document_date"]
    assert dated.quote_shows_value is True
    (place,) = dated.locations
    assert place.page == 1 and place.boxes
    assert found["contact"].locations == ()  # no quote
    assert found["contact"].quote_shows_value is None
    assert locator.calls == [["Musterstadt, 31.03.2026"]]


async def test_without_an_archive_the_quotes_stay_unlocated(world: World) -> None:
    await seed(world)
    document = await document_with(world)
    locator = FakeTextLocator([INVOICE])
    outcome = await judge(world, document, model(proposal()), locator=locator)
    assert isinstance(outcome, MetadataResult)
    assert outcome.result.outcome is Outcome.OK
    assert checks(outcome.result)["document_date"].locations == ()


@pytest.mark.parametrize(
    ("evidence", "shows"),
    [("Stadtwerke Musterstadt GmbH", True), ("Postfach 12 34, 12345 Musterstadt", False)],
)
async def test_a_quote_may_lead_to_the_contact_without_naming_it(
    world: World, evidence: str, shows: bool
) -> None:
    await seed(world)
    sender = {"value": "Stadtwerke Musterstadt GmbH", "evidence": evidence}
    result = await classify(world, proposal(sender=sender))
    assert checks(result.result)["contact"].quote_shows_value is shows


async def test_field_quotes_show_amounts_and_dates_but_never_yes_or_no(world: World) -> None:
    paid = {"value": True, "evidence": "Zahlbar bis 15.04.2026."}
    number = {"value": "R-2026-0815", "evidence": None}
    seeded, result = await extract(world, extraction(a1=paid, a4=number))
    assert field_check(seeded, result, "Rechnungsbetrag").quote_shows_value is True
    assert field_check(seeded, result, "Fällig am").quote_shows_value is True
    assert field_check(seeded, result, "Bezahlt").quote_shows_value is False
    assert field_check(seeded, result, "Rechnungsnummer").quote_shows_value is None
