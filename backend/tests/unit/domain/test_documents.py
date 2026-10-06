from datetime import date, timedelta
from decimal import Decimal

import pytest

from papiq.core.domain.attributes import AttributeDefinition, AttributeType
from papiq.core.domain.documents import UNSET, Document, DocumentChanges, Sha256
from papiq.core.domain.errors import NotFoundError, ValidationError
from papiq.core.domain.events import (
    DocumentDeleted,
    DocumentFiled,
    DocumentReceived,
    DocumentUpdated,
    StepCompleted,
)
from papiq.core.domain.ids import ContactId, DocumentTypeId, TagId, new_id
from papiq.core.domain.pipeline import Step
from tests import builders
from tests.builders import FAILED, NOW, OK, UNCERTAIN

LATER = NOW + timedelta(minutes=5)


def test_sha256_is_lower_case_hex() -> None:
    assert Sha256.of(b"abc").hex.startswith("ba7816bf")
    assert str(Sha256.of(b"abc")) == Sha256.of(b"abc").hex
    for invalid in ("ABC", "g" * 64, "a" * 63, "A" * 64):
        with pytest.raises(ValidationError):
            Sha256(invalid)


def test_receive_records_received_and_step_completed() -> None:
    owner = builders.user()
    document = Document.receive(
        owner_id=owner.id,
        drawer_id=builders.default_drawer(owner).id,
        sha256=builders.sha256(),
        original_filename="2026-10 Invoice.pdf",
        media_type="application/pdf",
        result=UNCERTAIN,
        now=NOW,
    )
    assert document.title == "2026-10 Invoice"
    events = document.pull_events()
    assert [type(event) for event in events] == [DocumentReceived, StepCompleted]
    assert isinstance(events[1], StepCompleted)
    assert events[1].step is Step.RECEIVE


def test_receive_cannot_fail() -> None:
    owner = builders.user()
    with pytest.raises(ValidationError):
        Document.receive(
            owner_id=owner.id,
            drawer_id=builders.default_drawer(owner).id,
            sha256=builders.sha256(),
            original_filename="a.pdf",
            media_type="application/pdf",
            result=FAILED,
            now=NOW,
        )


def test_metadata_change_records_changed_fields() -> None:
    document = builders.document(builders.user(), builders.drawer(builders.user()))
    contact, tag = ContactId(new_id()), TagId(new_id())
    changed = document.apply_changes(
        DocumentChanges(
            title=" Electricity ",
            contact_id=contact,
            tag_ids=frozenset({tag}),
            document_date=date(2026, 9, 30),
        ),
        {},
        LATER,
    )
    assert changed == ("title", "contact_id", "tag_ids", "document_date")
    assert document.title == "Electricity"
    assert document.contact_id == contact
    assert document.tag_ids == {tag}
    assert document.document_date == date(2026, 9, 30)
    assert document.updated_at == LATER
    (event,) = document.pull_events()
    assert isinstance(event, DocumentUpdated)
    assert event.fields == changed


def test_unchanged_metadata_records_nothing() -> None:
    document = builders.document(builders.user(), builders.drawer(builders.user()))
    assert document.apply_changes(DocumentChanges(title="scan", contact_id=None), {}, LATER) == ()
    assert document.pull_events() == []
    assert document.updated_at == NOW
    assert DocumentChanges().title is UNSET


def test_attribute_values_are_validated_and_scoped() -> None:
    invoice, letter = DocumentTypeId(new_id()), DocumentTypeId(new_id())
    total = AttributeDefinition.create(
        name="Total", data_type=AttributeType.NUMBER, now=NOW, document_type_ids=[invoice]
    )
    note = AttributeDefinition.create(name="Note", data_type=AttributeType.TEXT, now=NOW)
    definitions = {total.id: total, note.id: note}
    document = builders.document(builders.user(), builders.drawer(builders.user()))

    with pytest.raises(ValidationError, match="does not apply"):
        document.apply_changes(DocumentChanges(attributes={total.id: 10}), definitions, LATER)
    with pytest.raises(NotFoundError):
        document.apply_changes(
            DocumentChanges(
                attributes={
                    AttributeDefinition.create(
                        name="x", data_type=AttributeType.TEXT, now=NOW
                    ).id: "x"
                }
            ),
            definitions,
            LATER,
        )

    document.apply_changes(
        DocumentChanges(document_type_id=invoice, attributes={total.id: 10, note.id: "paid"}),
        definitions,
        LATER,
    )
    assert document.attributes == {total.id: Decimal(10), note.id: "paid"}

    with pytest.raises(ValidationError, match="does not accept"):
        document.apply_changes(DocumentChanges(attributes={note.id: ""}), definitions, LATER)
    assert document.attributes[note.id] == "paid"

    changed = document.apply_changes(DocumentChanges(document_type_id=letter), definitions, LATER)
    assert changed == ("document_type_id", "attributes")
    assert document.attributes == {note.id: "paid"}

    document.apply_changes(DocumentChanges(attributes={note.id: None}), definitions, LATER)
    assert document.attributes == {}


def test_failed_validation_changes_nothing_but_earlier_fields() -> None:
    note = AttributeDefinition.create(name="Note", data_type=AttributeType.TEXT, now=NOW)
    document = builders.document(builders.user(), builders.drawer(builders.user()))
    with pytest.raises(ValidationError):
        document.apply_changes(DocumentChanges(attributes={note.id: 5}), {note.id: note}, LATER)
    assert document.attributes == {}
    assert document.pull_events() == []


def test_move_records_filed() -> None:
    owner = builders.user()
    document = builders.document(owner, builders.drawer(owner))
    target = builders.drawer(owner)
    document.move_to(target.id, LATER)
    document.move_to(target.id, LATER)
    (event,) = document.pull_events()
    assert isinstance(event, DocumentFiled)
    assert event.drawer_id == target.id
    assert document.drawer_id == target.id


def test_delete_records_deleted() -> None:
    document = builders.document(builders.user(), builders.drawer(builders.user()))
    document.delete(LATER)
    (event,) = document.pull_events()
    assert isinstance(event, DocumentDeleted)
    assert event.occurred_at == LATER


def test_ok_result_constant_is_ok() -> None:
    assert OK.reason is None
