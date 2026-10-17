"""Filters and counts on tags and groups: a tag stands for itself and the tags below it, a group
for the documents whose contact or type is in it or a group below it."""

from dataclasses import dataclass

import pytest

from papiq.core.domain.documents import Document, DocumentChanges
from papiq.core.domain.ids import DocumentId, TagId
from papiq.core.domain.master_data import Tag, TagScope
from papiq.core.domain.users import Role, User
from papiq.core.ports import CountBy, DocumentFilter
from papiq.core.services.search import SearchService
from tests.unit.services.conftest import World
from tests.unit.services.search_support import Setup


@dataclass
class Tags:
    setup: Setup
    admin: User
    owner: User
    insurance: Tag
    health: Tag
    car: Tag
    insurer: Tag
    health_insurer: Tag
    bills: Tag
    medical: Tag
    with_health: Document
    with_car_and_health: Document
    of_insurer: Document
    plain: Document

    async def listed(self, filter: DocumentFilter, user: User | None = None) -> set[DocumentId]:
        views = await self.setup.world.documents.query((user or self.owner).id, filter)
        return {view.document.id for view in views}

    async def searched(self, filter: DocumentFilter) -> set[DocumentId]:
        search = SearchService(self.setup.world.uow, self.setup.index)
        page = await search.search(self.owner.id, "Schreiben", filter)
        return {item.document.id for item in page.items}


def sets(*tags: Tag) -> tuple[frozenset[TagId], ...]:
    return tuple(frozenset({tag.id}) for tag in tags)


@pytest.fixture
async def tags(world: World) -> Tags:
    """Insurance with Health and Car; the contact group Insurer with Health insurer; the type
    group Bills with Medical bills. Documents: one with Health, one with Car and Health, one
    of a contact in Health insurer with a type in Medical bills, and one without."""
    setup = Setup(world, embeddings=None)
    admin, owner = await world.user(role=Role.ADMIN), await world.user()
    master = setup.master_data
    insurance = await master.create_tag(admin.id, "Insurance")
    health = await master.create_tag(admin.id, "Health", parent_id=insurance.id)
    car = await master.create_tag(admin.id, "Car", parent_id=insurance.id)
    insurer = await master.create_tag(admin.id, "Insurer", scope=TagScope.CONTACT)
    health_insurer = await master.create_tag(
        admin.id, "Health insurer", scope=TagScope.CONTACT, parent_id=insurer.id
    )
    bills = await master.create_tag(admin.id, "Bills", scope=TagScope.DOCUMENT_TYPE)
    medical = await master.create_tag(
        admin.id, "Medical bills", scope=TagScope.DOCUMENT_TYPE, parent_id=bills.id
    )
    contact = await master.create_contact(admin.id, "Nord", group_ids=[health_insurer.id])
    kind = await master.create_document_type(admin.id, "Invoice", group_ids=[medical.id])
    documents = []
    for text, changes in (
        ("Schreiben a", DocumentChanges(tag_ids=frozenset({health.id}))),
        ("Schreiben b", DocumentChanges(tag_ids=frozenset({car.id, health.id}))),
        ("Schreiben c", DocumentChanges(contact_id=contact.id, document_type_id=kind.id)),
        ("Schreiben d", DocumentChanges()),
    ):
        document = await setup.document(owner, text)
        await world.documents.update_metadata(owner.id, document.id, changes)
        documents.append(document)
    await setup.settle()
    return Tags(
        setup,
        admin,
        owner,
        insurance,
        health,
        car,
        insurer,
        health_insurer,
        bills,
        medical,
        *documents,
    )


async def test_a_tag_finds_the_documents_with_a_tag_below_it(tags: Tags) -> None:
    both = {tags.with_health.id, tags.with_car_and_health.id}
    for found in (tags.listed, tags.searched):
        assert await found(DocumentFilter(tags=sets(tags.insurance))) == both
        assert await found(DocumentFilter(tags=sets(tags.car))) == {tags.with_car_and_health.id}
        assert await found(DocumentFilter(tags=sets(tags.insurance, tags.car))) == {
            tags.with_car_and_health.id
        }


async def test_a_group_finds_the_documents_of_its_contacts_and_types(tags: Tags) -> None:
    for found in (tags.listed, tags.searched):
        for filter in (
            DocumentFilter(contact_groups=sets(tags.insurer)),
            DocumentFilter(contact_groups=sets(tags.health_insurer)),
            DocumentFilter(document_type_groups=sets(tags.bills)),
            DocumentFilter(
                contact_groups=sets(tags.insurer), document_type_groups=sets(tags.medical)
            ),
        ):
            assert await found(filter) == {tags.of_insurer.id}
        assert await found(DocumentFilter(contact_groups=sets(tags.bills))) == set()
        assert (
            await found(
                DocumentFilter(contact_groups=sets(tags.insurer), tags=sets(tags.insurance))
            )
            == set()
        )


async def test_the_search_checks_groups_against_the_database(tags: Tags) -> None:
    document = await tags.setup.world.documents.get(tags.owner.id, tags.of_insurer.id)
    assert document.contact_id is not None
    await tags.setup.master_data.change_contact(tags.admin.id, document.contact_id, group_ids=[])
    # The index still holds the group: its refresh has not run yet.
    assert tags.setup.index.documents[document.id].contact_group_ids
    assert await tags.searched(DocumentFilter(contact_groups=sets(tags.insurer))) == set()


async def test_counts_take_each_document_once_at_a_tag_above(tags: Tags) -> None:
    documents = tags.setup.world.documents
    counts = await documents.document_counts(tags.owner.id, CountBy.TAG)
    assert counts == {
        tags.insurance.id: 2,
        tags.health.id: 2,
        tags.car.id: 1,
        tags.insurer.id: 1,
        tags.health_insurer.id: 1,
        tags.bills.id: 1,
        tags.medical.id: 1,
    }
    stranger = await tags.setup.world.user()
    assert await documents.document_counts(stranger.id, CountBy.TAG) == {}
    every = await documents.document_counts(tags.admin.id, CountBy.TAG, all_users=True)
    assert every == counts
