"""Topics: enabled at contacts, pointed to by identifiers, carried by documents."""

import asyncio

import pytest

from papiq.core.domain.documents import Document, DocumentChanges
from papiq.core.domain.errors import ConflictError, NotFoundError, ValidationError
from papiq.core.domain.identifiers import Identifier, IdentifierKind
from papiq.core.domain.ids import ContactId, TagId, new_id
from papiq.core.domain.master_data import Contact, Tag, TagScope
from papiq.core.domain.users import Role, User
from papiq.core.ports import CountBy, DocumentFilter
from tests.builders import incoming
from tests.unit.services.conftest import World

TOPIC = TagScope.TOPIC


async def _topics(world: World, admin: User) -> tuple[Tag, Tag, Tag]:
    """Work, Insurance and Health below it."""
    service = world.master_data
    work = await service.create_tag(admin.id, "Work", scope=TOPIC)
    insurance = await service.create_tag(admin.id, "Insurance", scope=TOPIC)
    health = await service.create_tag(admin.id, "Health", scope=TOPIC, parent_id=insurance.id)
    return work, insurance, health


async def _document(world: World, owner: User, name: str = "x") -> Document:
    document = await world.pipeline().receive(
        owner.id, incoming(f"%PDF-1.7 {name}".encode()), filename=f"{name}.pdf"
    )
    await world.drain()
    return document


async def _contact(world: World, admin: User, id: ContactId) -> Contact:
    return await world.master_data.get_contact(admin.id, id)


async def test_a_contact_enables_topics_and_its_identifiers_point_to_them(world: World) -> None:
    admin = await world.user(role=Role.ADMIN)
    service = world.master_data
    work, insurance, health = await _topics(world, admin)
    tag = await service.create_tag(admin.id, "Paid")
    number = Identifier.parse(IdentifierKind.PERSONNEL_NUMBER, "4711", topic_id=work.id)
    contact = await service.create_contact(
        admin.id, "Nord", identifiers=[number], topic_ids=[work.id, insurance.id, health.id]
    )
    # A topic and one below it are both enabled: a document may carry either.
    assert contact.topic_ids == {work.id, insurance.id, health.id}
    assert contact.identifiers == [number]
    with pytest.raises(ValidationError, match="no topic"):
        await service.change_contact(admin.id, contact.id, topic_ids=[tag.id])
    with pytest.raises(NotFoundError):
        await service.change_contact(admin.id, contact.id, topic_ids=[TagId(new_id())])
    other = Tag.create(name="Other", now=world.clock.now(), scope=TOPIC)
    with pytest.raises(ValidationError, match="topic"):
        await service.create_contact(
            admin.id,
            "Süd",
            identifiers=[
                Identifier.parse(IdentifierKind.IBAN, "DE89370400440532013000", topic_id=other.id)
            ],
        )
    # Attaching from a document's facts: with a topic of the contact, or none.
    policy = Identifier.parse(IdentifierKind.CUSTOMER_NUMBER, "KV-2026-1", topic_id=health.id)
    attached = await service.add_identifier(admin.id, contact.id, policy)
    assert {item.value: item.topic_id for item in attached.identifiers} == {
        "4711": work.id,
        "KV20261": health.id,
    }
    stranger = Identifier.parse(IdentifierKind.VAT_ID, "DE123456789", topic_id=other.id)
    with pytest.raises(ValidationError, match="topic"):
        await service.add_identifier(admin.id, contact.id, stranger)
    # Taking a topic away leaves its identifiers without one.
    changed = await service.change_contact(admin.id, contact.id, topic_ids=[work.id])
    assert {item.value: item.topic_id for item in changed.identifiers} == {
        "4711": work.id,
        "KV20261": None,
    }
    # Identifiers given with the topics are checked against the topics after the change.
    both = await service.change_contact(
        admin.id, contact.id, topic_ids=[work.id, health.id], identifiers=[number, policy]
    )
    assert {item.value: item.topic_id for item in both.identifiers} == {
        "4711": work.id,
        "KV20261": health.id,
    }


async def test_a_document_carries_a_topic_its_contact_enables(world: World) -> None:
    admin, owner = await world.user(role=Role.ADMIN), await world.user()
    service = world.master_data
    work, insurance, health = await _topics(world, admin)
    group = await service.create_tag(admin.id, "Insurer", scope=TagScope.CONTACT)
    nord = await service.create_contact(admin.id, "Nord", topic_ids=[work.id, health.id])
    sued = await service.create_contact(admin.id, "Süd", topic_ids=[work.id])
    document = await _document(world, owner)
    change = world.documents.update_metadata
    with pytest.raises(ValidationError, match="contact"):
        await change(owner.id, document.id, DocumentChanges(topic_id=work.id))
    changed = await change(
        owner.id, document.id, DocumentChanges(contact_id=nord.id, topic_id=health.id)
    )
    assert (changed.topic_id, changed.confirmed_at) == (health.id, world.clock.now())
    for wrong in (group.id, TagId(new_id())):
        with pytest.raises((ValidationError, NotFoundError)):
            await change(owner.id, document.id, DocumentChanges(topic_id=wrong))
    # A person who is no admin cannot enable a topic at the contact.
    with pytest.raises(ValidationError, match="does not enable"):
        await change(owner.id, document.id, DocumentChanges(topic_id=insurance.id))
    # An admin's choice enables it, in the same change.
    chosen = await change(admin.id, document.id, DocumentChanges(topic_id=insurance.id))
    assert chosen.topic_id == insurance.id
    assert (await _contact(world, admin, nord.id)).topic_ids == {
        work.id,
        insurance.id,
        health.id,
    }
    # Another contact: the topic stays if it enables it, else it goes.
    await change(owner.id, document.id, DocumentChanges(topic_id=work.id))
    kept = await change(owner.id, document.id, DocumentChanges(contact_id=sued.id))
    assert (kept.contact_id, kept.topic_id) == (sued.id, work.id)
    await change(owner.id, document.id, DocumentChanges(contact_id=nord.id, topic_id=health.id))
    cleared = await change(owner.id, document.id, DocumentChanges(contact_id=sued.id))
    assert (cleared.contact_id, cleared.topic_id) == (sued.id, None)
    await change(owner.id, document.id, DocumentChanges(topic_id=work.id))
    without = await change(owner.id, document.id, DocumentChanges(contact_id=None))
    assert (without.contact_id, without.topic_id) == (None, None)
    # Removing the topic works without a contact.
    await change(owner.id, document.id, DocumentChanges(contact_id=sued.id, topic_id=work.id))
    assert (await change(owner.id, document.id, DocumentChanges(topic_id=None))).topic_id is None


async def test_a_topic_stays_enabled_while_documents_of_the_contact_carry_it(
    world: World,
) -> None:
    admin, owner = await world.user(role=Role.ADMIN), await world.user()
    service = world.master_data
    work, _, health = await _topics(world, admin)
    number = Identifier.parse(IdentifierKind.PERSONNEL_NUMBER, "4711", topic_id=work.id)
    nord = await service.create_contact(
        admin.id, "Nord", identifiers=[number], topic_ids=[work.id, health.id]
    )
    sued = await service.create_contact(admin.id, "Süd", topic_ids=[work.id])
    document = await _document(world, owner)
    await world.documents.update_metadata(
        owner.id, document.id, DocumentChanges(contact_id=nord.id, topic_id=work.id)
    )
    with pytest.raises(ConflictError, match="Work"):
        await service.change_contact(admin.id, nord.id, topic_ids=[health.id])
    # Another contact's documents do not hold it back.
    assert (await service.change_contact(admin.id, sued.id, topic_ids=[])).topic_ids == set()
    await world.documents.update_metadata(owner.id, document.id, DocumentChanges(topic_id=None))
    changed = await service.change_contact(admin.id, nord.id, topic_ids=[health.id])
    assert changed.identifiers == [Identifier(number.kind, number.value)]


async def test_a_topic_is_deleted_only_unused(world: World) -> None:
    admin, owner = await world.user(role=Role.ADMIN), await world.user()
    service = world.master_data
    work, insurance, health = await _topics(world, admin)
    with pytest.raises(ConflictError, match="below"):
        await service.delete_tag(admin.id, insurance.id)
    nord = await service.create_contact(admin.id, "Nord", topic_ids=[work.id, health.id])
    with pytest.raises(ConflictError, match="Nord"):
        await service.delete_tag(admin.id, health.id)
    document = await _document(world, owner)
    await world.documents.update_metadata(
        owner.id, document.id, DocumentChanges(contact_id=nord.id, topic_id=work.id)
    )
    with pytest.raises(ConflictError, match="in use"):
        await service.delete_tag(admin.id, work.id)
    await world.documents.update_metadata(owner.id, document.id, DocumentChanges(topic_id=None))
    await service.change_contact(admin.id, nord.id, topic_ids=[])
    for topic in (work, health, insurance):
        await service.delete_tag(admin.id, topic.id)
    assert await service.list_tags(admin.id, TOPIC) == []


async def test_documents_are_found_and_counted_by_a_topic_and_those_below_it(
    world: World,
) -> None:
    admin, owner = await world.user(role=Role.ADMIN), await world.user()
    work, insurance, health = await _topics(world, admin)
    nord = await world.master_data.create_contact(
        admin.id, "Nord", topic_ids=[work.id, insurance.id, health.id]
    )
    documents = []
    for name, topic in (("a", work), ("b", insurance), ("c", health), ("d", None)):
        document = await _document(world, owner, name)
        changes = DocumentChanges(contact_id=nord.id, topic_id=None if topic is None else topic.id)
        documents.append(await world.documents.update_metadata(owner.id, document.id, changes))
    numbers = {document.id: number for number, document in enumerate(documents)}

    async def found(*topics: Tag) -> set[int]:
        filter = DocumentFilter(topics=frozenset(topic.id for topic in topics))
        listed = await world.documents.query(owner.id, filter)
        return {numbers[view.document.id] for view in listed}

    assert await found(work) == {0}
    assert await found(insurance) == {1, 2}
    assert await found(health) == {2}
    # Several topics: one of them (a document has one), each with those below it.
    assert await found(work, health) == {0, 2}
    assert await found(work, insurance) == {0, 1, 2}
    counts = await world.documents.document_counts(owner.id, CountBy.TAG)
    assert {key: counts.get(key) for key in (work.id, insurance.id, health.id)} == {
        work.id: 1,
        insurance.id: 2,
        health.id: 1,
    }


async def test_a_topic_is_not_given_while_its_contact_gives_it_up(world: World) -> None:
    """Both at once: one waits for the other (`contact_topics_lock`) and sees it."""
    admin, owner = await world.user(role=Role.ADMIN), await world.user()
    work, _, _ = await _topics(world, admin)
    nord = await world.master_data.create_contact(admin.id, "Nord", topic_ids=[work.id])
    document = await _document(world, owner)
    await world.documents.update_metadata(
        owner.id, document.id, DocumentChanges(contact_id=nord.id)
    )
    results = await asyncio.gather(
        world.master_data.change_contact(admin.id, nord.id, topic_ids=[]),
        world.documents.update_metadata(owner.id, document.id, DocumentChanges(topic_id=work.id)),
        return_exceptions=True,
    )
    assert sorted(type(result).__name__ for result in results) in (
        ["Contact", "ValidationError"],
        ["ConflictError", "Document"],
    ), results
