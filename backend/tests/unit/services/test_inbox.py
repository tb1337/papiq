"""The inbox: open fields, the review, and confirming."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest

from papiq.core.domain.classification import FieldCheck, checks_to_json, field_key
from papiq.core.domain.documents import Document, DocumentChanges
from papiq.core.domain.drawers import ShareLevel
from papiq.core.domain.errors import (
    InvalidTransitionError,
    NotFoundError,
    OpenFieldsError,
    PermissionDeniedError,
    ValidationError,
)
from papiq.core.domain.fields import FieldDefinition, FieldType, Money
from papiq.core.domain.ids import ContactId, DrawerId, UserId
from papiq.core.domain.master_data import Contact
from papiq.core.domain.pipeline import Lane, Outcome, ProcessingStatus, Step, StepResult
from papiq.core.domain.users import User
from papiq.core.services.inbox import PERSON, OpenStep, decide, field_checks
from papiq.core.services.pipeline import PipelineService
from tests.builders import FAILED, NOW, document, incoming
from tests.unit.services.conftest import Returns, World

UNSURE = "not shown in the text"
DOCUMENT = document(UserId(UUID(int=1)), DrawerId(UUID(int=2)))


@dataclass
class Scene:
    owner: User
    contact: Contact
    amount: FieldDefinition
    pipeline: PipelineService
    document: Document


def classified(contact: Contact) -> StepResult:
    checks = [
        FieldCheck(
            field="contact",
            outcome=Outcome.UNCERTAIN,
            confidence=0.8,
            reason="similar",
            proposed="Stadtwerk",
            suggestion=str(contact.id),
        ),
        FieldCheck(field="tags", outcome=Outcome.OK, confidence=1, value=[]),
        FieldCheck(field="document_date", outcome=Outcome.UNCERTAIN, confidence=0, reason=UNSURE),
    ]
    return StepResult(
        outcome=Outcome.UNCERTAIN,
        reason="contact, document_date",
        model_version="fake-llm 1",
        input={"truncated": True},
        output={"fields": checks_to_json(checks)},
    )


def extracted(amount: FieldDefinition) -> StepResult:
    check = FieldCheck(
        field=field_key(amount.id),
        outcome=Outcome.UNCERTAIN,
        confidence=0,
        reason=UNSURE,
        proposed={"amount": "84.20", "currency": "EUR"},
        suggestion={"amount": "84.20", "currency": "EUR"},
    )
    return StepResult(
        outcome=Outcome.UNCERTAIN,
        reason="Betrag",
        model_version="fake-llm 1",
        output={"fields": checks_to_json([check])},
    )


async def scene(world: World) -> Scene:
    owner = await world.user()
    contact = Contact.create(name="Stadtwerke", now=NOW)
    amount = FieldDefinition.create(name="Betrag", data_type=FieldType.AMOUNT, now=NOW)
    async with world.uow() as uow:
        await uow.contacts.add(contact)
        await uow.fields.add(amount)
        await uow.commit()
    pipeline = world.pipeline(
        {
            Step.CLASSIFY: Returns(classified(contact)),
            Step.EXTRACT_FIELDS: Returns(extracted(amount)),
        }
    )
    document = await pipeline.receive(owner.id, incoming(b"%PDF-1.7\n%%EOF\n"), filename="a.pdf")
    await world.drain(pipeline)
    stored = await world.documents.get(owner.id, document.id)
    return Scene(owner, contact, amount, pipeline, stored)


async def test_an_uncertain_document_is_in_the_inbox(world: World) -> None:
    s = await scene(world)
    assert s.document.processing.status is ProcessingStatus.REVIEW
    (item,) = await world.documents.inbox(s.owner.id)
    assert item.document.id == s.document.id
    assert [(step.step, step.outcome) for step in item.open] == [
        (Step.CLASSIFY, Outcome.UNCERTAIN),
        (Step.EXTRACT_FIELDS, Outcome.UNCERTAIN),
    ]
    assert [check.field for check in item.open[0].fields] == ["contact", "document_date"]
    assert item.open[1].fields[0].field == field_key(s.amount.id)
    assert await world.documents.inbox((await world.user()).id) == []


async def test_the_review_shows_the_model_runs(world: World) -> None:
    s = await scene(world)
    review = await world.documents.review(s.owner.id, s.document.id)
    classify, extract = review.steps
    assert (classify.step, classify.truncated, classify.model_version) == (
        Step.CLASSIFY,
        True,
        "fake-llm 1",
    )
    assert [check.field for check in classify.fields] == ["contact", "tags", "document_date"]
    assert extract.truncated is None
    assert len(review.open) == 2


async def test_confirming_needs_a_decision_on_every_open_field(world: World) -> None:
    s = await scene(world)
    with pytest.raises(OpenFieldsError) as error:
        await s.pipeline.confirm(s.owner.id, s.document.id, DocumentChanges())
    assert error.value.fields == (
        "contact",
        "document_date",
        f"{field_key(s.amount.id)} (Betrag)",
    )
    with pytest.raises(OpenFieldsError) as error:
        await s.pipeline.confirm(
            s.owner.id, s.document.id, DocumentChanges(), accept_suggestions=True
        )
    assert error.value.fields == ("document_date",)
    unchanged = await world.documents.get(s.owner.id, s.document.id)
    assert unchanged.processing.status is ProcessingStatus.REVIEW


async def test_a_confirmed_document_is_filed(world: World) -> None:
    s = await scene(world)
    confirmed = await s.pipeline.confirm(
        s.owner.id,
        s.document.id,
        DocumentChanges(document_date=date(2026, 3, 31)),
        accept_suggestions=True,
    )
    assert confirmed.processing.status is ProcessingStatus.PROCESSING
    assert confirmed.processing.current_step is Step.APPLY_RULES
    assert confirmed.lane is None
    await world.drain(s.pipeline)

    document = await world.documents.get(s.owner.id, s.document.id)
    assert document.lane is Lane.GREEN
    assert document.contact_id == s.contact.id
    assert document.document_date == date(2026, 3, 31)
    assert document.fields[s.amount.id] == Money(Decimal("84.20"), "EUR")
    assert "document.filed" in world.event_types()
    assert await world.documents.inbox(s.owner.id) == []

    log = await world.documents.processing_log(s.owner.id, s.document.id)
    confirmations = [entry for entry in log if entry.result.model_version == PERSON]
    assert [(entry.step, entry.run) for entry in confirmations] == [
        (Step.CLASSIFY, 2),
        (Step.EXTRACT_FIELDS, 2),
        (Step.APPLY_RULES, 2),
    ]
    assert confirmations[0].result.output == {
        "confirmed_by": str(s.owner.id),
        "outcome_before": "uncertain",
        "accepted": ["contact"],
        "entered": ["document_date"],
        "kept": [],
    }
    # What the owner changed, for the rules that run next.
    assert confirmations[2].result.output["changed"] == sorted(
        ["contact", "document_date", f"field:{s.amount.id}"]
    )
    # The model's proposals stay readable, for the rules.
    assert field_checks(log)["contact"].proposed == "Stadtwerk"


async def contacts(world: World) -> dict[str, list[str]]:
    async with world.uow() as uow:
        return {item.name: item.aliases for item in await uow.contacts.list_all()}


async def test_accepting_the_suggested_contact_learns_the_name_read(world: World) -> None:
    s = await scene(world)
    await s.pipeline.confirm(
        s.owner.id,
        s.document.id,
        DocumentChanges(document_date=date(2026, 3, 31)),
        accept_suggestions=True,
    )
    assert await contacts(world) == {"Stadtwerke": ["Stadtwerk"]}
    log = await world.documents.processing_log(s.owner.id, s.document.id)
    rules = next(
        entry
        for entry in log
        if entry.step is Step.APPLY_RULES and entry.result.model_version == PERSON
    )
    assert rules.result.output["learned_alias"] == {
        "contact_id": str(s.contact.id),
        "alias": "Stadtwerk",
    }


async def test_choosing_another_contact_learns_there_by_any_user(world: World) -> None:
    """The owner is no admin; the alias moves from the suggested contact."""
    s = await scene(world)
    chosen = Contact.create(name="Stadtwerk Netz", now=NOW)
    async with world.uow() as uow:
        await uow.contacts.add(chosen)
        suggested = await uow.contacts.get(s.contact.id)
        suggested.set_aliases(["Stadtwerk"])
        await uow.contacts.update(suggested)
        await uow.commit()
    await s.pipeline.confirm(
        s.owner.id,
        s.document.id,
        DocumentChanges(contact_id=chosen.id, document_date=date(2026, 3, 31)),
        accept_suggestions=True,
    )
    assert await contacts(world) == {"Stadtwerk Netz": ["Stadtwerk"], "Stadtwerke": []}


async def test_an_unrelated_name_is_not_learned(world: World) -> None:
    """The model read something else (often the recipient): no alias for the chosen one."""
    s = await scene(world)
    chosen = Contact.create(name="Gemeindewerke", now=NOW)
    async with world.uow() as uow:
        await uow.contacts.add(chosen)
        await uow.commit()
    await s.pipeline.confirm(
        s.owner.id,
        s.document.id,
        DocumentChanges(contact_id=chosen.id, document_date=date(2026, 3, 31)),
        accept_suggestions=True,
    )
    assert await contacts(world) == {"Gemeindewerke": [], "Stadtwerke": []}


async def test_nothing_is_learned_without_a_chosen_contact(world: World) -> None:
    s = await scene(world)
    await s.pipeline.confirm(
        s.owner.id,
        s.document.id,
        DocumentChanges(contact_id=None, document_date=date(2026, 3, 31)),
        accept_suggestions=True,
    )
    assert await contacts(world) == {"Stadtwerke": []}
    log = await world.documents.processing_log(s.owner.id, s.document.id)
    assert all("learned_alias" not in entry.result.output for entry in log)


async def test_values_set_meanwhile_are_kept(world: World) -> None:
    """A value the owner set after the run (PATCH) decides its field; suggestions fill only
    the empty ones."""
    s = await scene(world)
    chosen = Contact.create(name="Gemeindewerke", now=NOW)
    async with world.uow() as uow:
        await uow.contacts.add(chosen)
        await uow.commit()
    await world.documents.update_metadata(
        s.owner.id, s.document.id, DocumentChanges(contact_id=chosen.id)
    )
    await s.pipeline.confirm(
        s.owner.id,
        s.document.id,
        DocumentChanges(document_date=date(2026, 3, 31)),
        accept_suggestions=True,
    )
    await world.drain(s.pipeline)
    document = await world.documents.get(s.owner.id, s.document.id)
    assert document.contact_id == chosen.id
    assert document.fields[s.amount.id] == Money(Decimal("84.20"), "EUR")
    log = await world.documents.processing_log(s.owner.id, s.document.id)
    confirmed = next(entry for entry in log if entry.result.model_version == PERSON)
    assert confirmed.result.output["kept"] == ["contact"]
    assert confirmed.result.output["accepted"] == []
    # Set outside the review: nothing is learned.
    assert await contacts(world) == {"Gemeindewerke": [], "Stadtwerke": []}


async def test_after_a_type_correction_fields_are_extracted_again(world: World) -> None:
    s = await scene(world)
    await s.pipeline.confirm(
        s.owner.id,
        s.document.id,
        DocumentChanges(contact_id=None, document_date=None),
        resume_at=Step.EXTRACT_FIELDS,
    )
    await world.drain(s.pipeline)
    document = await world.documents.get(s.owner.id, s.document.id)
    assert document.processing.status is ProcessingStatus.REVIEW  # extraction is uncertain again
    log = await world.documents.processing_log(s.owner.id, s.document.id)
    assert [entry.step for entry in log if entry.result.model_version == PERSON] == [
        Step.CLASSIFY,
        Step.APPLY_RULES,
    ]


async def test_a_red_document_can_be_taken_over(world: World) -> None:
    owner = await world.user()
    pipeline = world.pipeline({Step.CLASSIFY: Returns(FAILED)})
    document = await pipeline.receive(owner.id, incoming(b"%PDF-1.7\n%%EOF\n"), filename="a.pdf")
    await world.drain(pipeline)
    (item,) = await world.documents.inbox(owner.id)
    assert (item.open[0].step, item.open[0].outcome, item.open[0].fields) == (
        Step.CLASSIFY,
        Outcome.FAILED,
        (),
    )
    assert item.open[0].reason == FAILED.reason
    await pipeline.confirm(owner.id, document.id, DocumentChanges())
    await world.drain(pipeline)
    stored = await world.documents.get(owner.id, document.id)
    assert stored.lane is Lane.GREEN
    log = await world.documents.processing_log(owner.id, document.id)
    taken = [entry for entry in log if entry.result.model_version == PERSON]
    assert [entry.step for entry in taken] == [
        Step.CLASSIFY,
        Step.EXTRACT_FIELDS,
        Step.APPLY_RULES,
    ]
    assert taken[0].result.output["outcome_before"] == "failed"
    assert taken[1].result.output["outcome_before"] is None


async def test_only_documents_in_the_inbox_can_be_confirmed(world: World) -> None:
    owner = await world.user()
    pipeline = world.pipeline()
    document = await pipeline.receive(owner.id, incoming(b"%PDF-1.7\n%%EOF\n"), filename="a.pdf")
    with pytest.raises(InvalidTransitionError, match="not waiting for confirmation"):
        await pipeline.confirm(owner.id, document.id, DocumentChanges())  # processing
    await world.drain(pipeline)
    with pytest.raises(InvalidTransitionError, match="not waiting for confirmation"):
        await pipeline.confirm(owner.id, document.id, DocumentChanges())  # green


async def test_the_inbox_is_the_owners(world: World) -> None:
    s = await scene(world)
    stranger = await world.user()
    with pytest.raises(NotFoundError):
        await world.documents.review(stranger.id, s.document.id)
    with pytest.raises(NotFoundError):
        await s.pipeline.confirm(stranger.id, s.document.id, DocumentChanges())

    # A green document in a shared drawer: readable, but the review is the owner's.
    reader = await world.user()
    drawer = await world.drawers.create(s.owner.id, "Shared")
    await world.drawers.share(s.owner.id, drawer.id, reader.id, ShareLevel.READ_WRITE)
    pipeline = world.pipeline()
    green = await pipeline.receive(
        s.owner.id, incoming(b"%PDF-1.7 green\n"), filename="b.pdf", drawer=drawer.id
    )
    await world.drain(pipeline)
    with pytest.raises(PermissionDeniedError):
        await world.documents.review(reader.id, green.id)
    with pytest.raises(PermissionDeniedError):
        await pipeline.confirm(reader.id, green.id, DocumentChanges())


async def test_a_confirmed_document_becomes_visible_to_the_drawers_readers(world: World) -> None:
    s = await scene(world)
    reader = await world.user()
    drawer = await world.drawers.create(s.owner.id, "Shared")
    await world.drawers.share(s.owner.id, drawer.id, reader.id, ShareLevel.READ)
    document = await s.pipeline.receive(
        s.owner.id, incoming(b"%PDF-1.7 shared\n"), filename="b.pdf", drawer=drawer.id
    )
    await world.drain(s.pipeline)
    assert await world.documents.list_visible(reader.id) == []  # yellow: the owner's
    await s.pipeline.confirm(
        s.owner.id,
        document.id,
        DocumentChanges(document_date=date(2026, 3, 31)),
        accept_suggestions=True,
    )
    await world.drain(s.pipeline)
    (visible,) = await world.documents.list_visible(reader.id)
    assert (visible.id, visible.lane) == (document.id, Lane.GREEN)


def test_suggestions_that_do_not_fit_are_refused() -> None:
    check = FieldCheck(
        field="contact",
        outcome=Outcome.UNCERTAIN,
        confidence=0.8,
        reason="similar",
        suggestion="not an id",
    )
    open = [OpenStep(Step.CLASSIFY, Outcome.UNCERTAIN, "x", (check,))]
    with pytest.raises(ValidationError, match="cannot be taken"):
        decide(open, DOCUMENT, DocumentChanges(), accept_suggestions=True, definitions={})
    decision = decide(
        open,
        DOCUMENT,
        DocumentChanges(contact_id=ContactId(UUID(int=7))),
        accept_suggestions=True,
        definitions={},
    )
    assert decision.entered == {Step.CLASSIFY: ("contact",)}
    assert decision.accepted == {}


def test_fields_of_removed_fields_need_no_decision() -> None:
    gone = FieldDefinition.create(name="Alt", data_type=FieldType.TEXT, now=NOW)
    check = FieldCheck(
        field=field_key(gone.id), outcome=Outcome.UNCERTAIN, confidence=0, reason="x"
    )
    open = [OpenStep(Step.EXTRACT_FIELDS, Outcome.UNCERTAIN, "x", (check,))]
    decision = decide(open, DOCUMENT, DocumentChanges(), accept_suggestions=False, definitions={})
    assert decision.changes == DocumentChanges()
