from datetime import UTC, datetime

import pytest

from papiq.core.domain.errors import ValidationError
from papiq.core.domain.identifiers import Identifier, IdentifierKind, IdentifierSource
from papiq.core.domain.master_data import (
    MAX_ALIASES,
    MAX_DESCRIPTION,
    MAX_GROUPS,
    MAX_TOPICS,
    Assignment,
    Contact,
    DocumentType,
    Tag,
    TagScope,
)

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize("kind", [Contact, DocumentType, Tag])
def test_master_data_has_a_name(kind: type[Contact | DocumentType | Tag]) -> None:
    item = kind.create(name=" Stadtwerke ", now=NOW)
    assert item.name == "Stadtwerke"
    item.rename("Stadtwerke Köln")
    assert item.name == "Stadtwerke Köln"
    with pytest.raises(ValidationError):
        item.rename(" ")
    with pytest.raises(ValidationError):
        kind.create(name="", now=NOW)


def test_contact_aliases() -> None:
    contact = Contact.create(name="Nord", now=NOW, aliases=[" Nord AG ", "nord ag", "Nord\n\x00KV"])
    assert contact.aliases == ["Nord AG", "Nord KV"]
    assert contact.is_named("nord kv") and not contact.is_named("Nord Leben")
    with pytest.raises(ValidationError):
        contact.set_aliases(["Nord"])
    with pytest.raises(ValidationError):
        contact.set_aliases([" "])
    with pytest.raises(ValidationError):
        contact.set_aliases(f"Alias {number}" for number in range(MAX_ALIASES + 1))
    contact.rename("Nord AG")  # an alias that becomes the name is no longer an alias
    assert contact.aliases == ["Nord KV"]


def test_document_type_description() -> None:
    kind = DocumentType.create(name="Pay slip", now=NOW, description="  ")
    assert kind.description is None
    kind.describe(" Entgeltbescheinigung ")
    assert kind.description == "Entgeltbescheinigung"
    with pytest.raises(ValidationError):
        kind.describe("x" * (MAX_DESCRIPTION + 1))


def test_a_tag_has_a_fixed_scope_and_defaults_by_scope() -> None:
    document = Tag.create(name="Paid", now=NOW)
    assert (document.scope, document.assignment, document.shown) == (
        TagScope.DOCUMENT,
        Assignment.AUTO,
        True,
    )
    assert document.is_target
    status = Tag.create(name="To pay", now=NOW, assignment=Assignment.MANUAL)
    assert not status.is_target
    group = Tag.create(name="Insurance", now=NOW, scope=TagScope.CONTACT)
    assert (group.assignment, group.shown, group.is_target) == (Assignment.MANUAL, False, False)
    shown = Tag.create(name="Invoice", now=NOW, scope=TagScope.DOCUMENT_TYPE, shown=True)
    assert shown.shown


def test_groups_are_set_by_hand_and_no_tag_is_its_own_parent() -> None:
    with pytest.raises(ValidationError, match="by hand"):
        Tag.create(name="Insurance", now=NOW, scope=TagScope.CONTACT, assignment=Assignment.AUTO)
    group = Tag.create(name="Insurance", now=NOW, scope=TagScope.CONTACT)
    with pytest.raises(ValidationError, match="by hand"):
        group.assign(Assignment.AUTO)
    with pytest.raises(ValidationError, match="own parent"):
        group.move(group.id)


def test_contacts_and_types_are_in_groups() -> None:
    group = Tag.create(name="Insurance", now=NOW, scope=TagScope.CONTACT)
    contact = Contact.create(name="Nord", now=NOW)
    assert contact.group_ids == frozenset()
    contact.set_groups([group.id, group.id])
    assert contact.group_ids == {group.id}
    kind = DocumentType.create(name="Invoice", now=NOW)
    kind.set_groups([group.id])
    assert kind.group_ids == {group.id}
    with pytest.raises(ValidationError, match="groups"):
        kind.set_groups(Tag.create(name=str(n), now=NOW).id for n in range(MAX_GROUPS + 1))


def test_a_topic_is_proposed_and_shown_unless_set_by_hand() -> None:
    topic = Tag.create(name="Work", now=NOW, scope=TagScope.TOPIC)
    assert (topic.assignment, topic.shown) == (Assignment.AUTO, True)
    assert topic.is_topic_target and not topic.is_target
    by_hand = Tag.create(
        name="Private", now=NOW, scope=TagScope.TOPIC, assignment=Assignment.MANUAL
    )
    assert not by_hand.is_topic_target
    assert not Tag.create(name="Paid", now=NOW).is_topic_target


def test_a_contact_enables_topics_and_its_identifiers_point_to_them() -> None:
    work, health = (Tag.create(name=name, now=NOW, scope=TagScope.TOPIC) for name in "WH")
    number = Identifier(IdentifierKind.PERSONNEL_NUMBER, "4711", topic_id=work.id)
    with pytest.raises(ValidationError, match="topic"):
        Contact.create(name="Nord", now=NOW, identifiers=[number])
    contact = Contact.create(name="Nord", now=NOW, topic_ids=[work.id], identifiers=[number])
    assert contact.identifiers == [number]
    policy = Identifier(IdentifierKind.CUSTOMER_NUMBER, "KV1", topic_id=health.id)
    with pytest.raises(ValidationError, match="topic"):
        contact.set_identifiers([number, policy])
    assert contact.enable_topic(health.id) and not contact.enable_topic(health.id)
    contact.set_identifiers([number, policy])
    # A learned one keeps a topic the contact enables, and loses one it does not.
    other = Tag.create(name="Other", now=NOW, scope=TagScope.TOPIC)
    contact.set_learned(
        [
            Identifier(IdentifierKind.EMAIL_DOMAIN, "nord.example", topic_id=health.id),
            Identifier(IdentifierKind.IBAN, "DE89370400440532013000", topic_id=other.id),
        ]
    )
    learned = [item for item in contact.identifiers if item.source is IdentifierSource.LEARNED]
    assert [item.topic_id for item in learned] == [None, health.id]
    # Taking a topic away leaves its identifiers without one.
    assert contact.set_topics([work.id]) == {health.id}
    assert [item.topic_id for item in contact.identifiers] == [None, work.id, None, None]
    with pytest.raises(ValidationError, match="topics"):
        contact.set_topics(Tag.create(name=str(n), now=NOW).id for n in range(MAX_TOPICS + 1))
