import asyncio
from collections.abc import Collection
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from papiq.core.domain.appearance import ContactLogo, FailedLogoFetch, LogoSource
from papiq.core.domain.classifier import ClassifierModel
from papiq.core.domain.documents import Document, DocumentChanges
from papiq.core.domain.errors import ConcurrencyError, ConflictError, NotFoundError
from papiq.core.domain.fields import FieldDefinition, FieldType, Money, Url
from papiq.core.domain.identifiers import Identifier, IdentifierKind, IdentifierSource
from papiq.core.domain.ids import DocumentId, SpaceId, TagId, new_id
from papiq.core.domain.master_data import Assignment, Contact, DocumentType, Tag, TagScope
from papiq.core.domain.periods import Period
from papiq.core.domain.permissions import can_read_document
from papiq.core.domain.pipeline import Lane, Node, NodeResult, NodeRun, Outcome, ProcessingStatus
from papiq.core.domain.spaces import ShareLevel, Space
from papiq.core.domain.users import User
from papiq.core.domain.verdict import KnownLabels
from papiq.core.ports import (
    NEWEST_FIRST,
    CountBy,
    DocumentFilter,
    DocumentOrder,
    DocumentPosition,
    SortKey,
    UnitOfWorkFactory,
)
from papiq.core.ports.repository import TagSets
from tests import builders
from tests.builders import FAILED, NOW, OK, UNCERTAIN


async def seed(
    uow_factory: UnitOfWorkFactory, *users: User, spaces: tuple[Space, ...] = ()
) -> None:
    async with uow_factory() as uow:
        for user in users:
            await uow.users.add(user)
        for space in spaces:
            await uow.spaces.add(space)
        await uow.commit()


async def owner_with_space(uow_factory: UnitOfWorkFactory) -> tuple[User, Space]:
    owner = builders.user()
    space = builders.default_space(owner)
    await seed(uow_factory, owner, spaces=(space,))
    return owner, space


def _repository_of(item: object) -> str:
    return {
        Contact: "contacts",
        DocumentType: "document_types",
        Tag: "tags",
    }[type(item)]


class UnitOfWorkContract:
    # --- transactions ---------------------------------------------------------------------------

    async def test_committed_changes_are_stored(self, uow_factory: UnitOfWorkFactory) -> None:
        user = builders.user()
        async with uow_factory() as uow:
            await uow.users.add(user)
            await uow.commit()
        async with uow_factory() as uow:
            assert await uow.users.get(user.id) == user

    async def test_without_commit_nothing_is_stored(self, uow_factory: UnitOfWorkFactory) -> None:
        user = builders.user()
        async with uow_factory() as uow:
            await uow.users.add(user)
            assert await uow.users.get(user.id) == user  # own changes are visible
        async with uow_factory() as uow:
            await uow.users.add(other := builders.user())
            await uow.rollback()
        async with uow_factory() as uow:
            assert await uow.users.find(user.id) is None
            assert await uow.users.find(other.id) is None

    async def test_exception_rolls_back(self, uow_factory: UnitOfWorkFactory) -> None:
        user = builders.user()
        with pytest.raises(LookupError):
            async with uow_factory() as uow:
                await uow.users.add(user)
                raise LookupError
        async with uow_factory() as uow:
            assert await uow.users.find(user.id) is None

    async def test_uncommitted_changes_are_invisible_to_others(
        self, uow_factory: UnitOfWorkFactory
    ) -> None:
        user = builders.user()
        async with uow_factory() as writer:
            await writer.users.add(user)
            async with uow_factory() as reader:
                assert await reader.users.find(user.id) is None
            await writer.commit()
        async with uow_factory() as reader:
            assert await reader.users.find(user.id) == user

    async def test_closed_after_commit(self, uow_factory: UnitOfWorkFactory) -> None:
        async with uow_factory() as uow:
            await uow.commit()
            with pytest.raises(RuntimeError):
                await uow.commit()

    async def test_reads_are_copies(self, uow_factory: UnitOfWorkFactory) -> None:
        owner, _ = await owner_with_space(uow_factory)
        async with uow_factory() as uow:
            loaded = await uow.users.get(owner.id)
            loaded.username = "changed"
            assert (await uow.users.get(owner.id)).username == owner.username
            await uow.commit()
        async with uow_factory() as uow:
            assert (await uow.users.get(owner.id)).username == owner.username

    async def test_failed_commit_stores_nothing(self, uow_factory: UnitOfWorkFactory) -> None:
        first = builders.user("taken")
        await seed(uow_factory, first)
        bystander = builders.user()
        with pytest.raises(ConflictError):
            async with uow_factory() as uow:
                await uow.users.add(bystander)
                await uow.users.add(builders.user("TAKEN"))
                await uow.commit()
        async with uow_factory() as uow:
            assert await uow.users.find(bystander.id) is None

    # --- versions -------------------------------------------------------------------------------

    async def test_update_increments_the_version(self, uow_factory: UnitOfWorkFactory) -> None:
        owner, _ = await owner_with_space(uow_factory)
        async with uow_factory() as uow:
            user = await uow.users.get(owner.id)
            user.username = "renamed"
            await uow.users.update(user)
            assert user.version == 2
            await uow.users.update(user)
            assert user.version == 3
            await uow.commit()
        async with uow_factory() as uow:
            stored = await uow.users.get(owner.id)
            assert (stored.username, stored.version) == ("renamed", 3)

    async def test_stale_update_is_rejected(self, uow_factory: UnitOfWorkFactory) -> None:
        owner, _ = await owner_with_space(uow_factory)
        async with uow_factory() as first, uow_factory() as second:
            mine = await first.users.get(owner.id)
            theirs = await second.users.get(owner.id)
            mine.username = "first"
            await first.users.update(mine)
            await first.commit()
            theirs.username = "second"
            with pytest.raises(ConcurrencyError):
                await second.users.update(theirs)
                await second.commit()
        async with uow_factory() as uow:
            assert (await uow.users.get(owner.id)).username == "first"

    async def test_concurrent_updates_one_wins(self, uow_factory: UnitOfWorkFactory) -> None:
        """Both units read the same version and update it; exactly one commit succeeds.

        The two units run as concurrent tasks, so an adapter may block the second write until
        the first unit commits (row locks) or detect the conflict on commit.
        """
        owner, _ = await owner_with_space(uow_factory)
        both_read = asyncio.Barrier(2)

        async def rename(name: str) -> str:
            async with uow_factory() as uow:
                user = await uow.users.get(owner.id)
                async with asyncio.timeout(10):
                    await both_read.wait()
                user.username = name
                await uow.users.update(user)
                await asyncio.sleep(0)
                await uow.commit()
            return name

        results = await asyncio.gather(rename("first"), rename("second"), return_exceptions=True)
        winners = [result for result in results if isinstance(result, str)]
        assert len(winners) == 1
        assert any(isinstance(result, ConcurrencyError) for result in results)
        async with uow_factory() as uow:
            stored = await uow.users.get(owner.id)
        assert (stored.username, stored.version) == (winners[0], 2)

    async def test_update_of_missing_entity(self, uow_factory: UnitOfWorkFactory) -> None:
        async with uow_factory() as uow:
            with pytest.raises(NotFoundError):
                await uow.users.update(builders.user())

    # --- lookups and uniqueness -----------------------------------------------------------------

    async def test_get_and_find_missing(self, uow_factory: UnitOfWorkFactory) -> None:
        missing = builders.user()
        async with uow_factory() as uow:
            assert await uow.users.find(missing.id) is None
            with pytest.raises(NotFoundError):
                await uow.users.get(missing.id)

    async def test_add_twice_is_a_conflict(self, uow_factory: UnitOfWorkFactory) -> None:
        user = builders.user()
        await seed(uow_factory, user)
        with pytest.raises(ConflictError):
            async with uow_factory() as uow:
                await uow.users.add(user)
                await uow.commit()

    async def test_usernames_are_unique_regardless_of_case(
        self, uow_factory: UnitOfWorkFactory
    ) -> None:
        await seed(uow_factory, builders.user("Alice"))
        async with uow_factory() as uow:
            found = await uow.users.find_by_username("ALICE")
            assert found is not None and found.username == "Alice"
            assert await uow.users.find_by_username("bob") is None
        with pytest.raises(ConflictError):
            await seed(uow_factory, builders.user("alice"))

    async def test_names_compare_with_unicode_case_folding(
        self, uow_factory: UnitOfWorkFactory
    ) -> None:
        """Not only ASCII: "Ärzte" and "ärzte" are the same name, as are "Straße" and "STRASSE"."""
        await seed(uow_factory, builders.user("Jürgen"))
        async with uow_factory() as uow:
            await uow.tags.add(Tag.create(name="Ärzte", now=NOW))
            await uow.contacts.add(Contact.create(name="Straße", now=NOW))
            await uow.commit()
        async with uow_factory() as uow:
            found = await uow.users.find_by_username("JÜRGEN")
            assert found is not None and found.username == "Jürgen"
            tag = await uow.tags.find_by_name("ärzte")
            assert tag is not None and tag.name == "Ärzte"
            contact = await uow.contacts.find_by_name("STRASSE")
            assert contact is not None and contact.name == "Straße"
        duplicates: list[User | Tag | Contact] = [
            builders.user("jürgen"),
            Tag.create(name="ÄRZTE", now=NOW),
            Contact.create(name="strasse", now=NOW),
        ]
        for duplicate in duplicates:
            with pytest.raises(ConflictError):
                async with uow_factory() as uow:
                    if isinstance(duplicate, User):
                        await uow.users.add(duplicate)
                    elif isinstance(duplicate, Tag):
                        await uow.tags.add(duplicate)
                    else:
                        await uow.contacts.add(duplicate)
                    await uow.commit()

    async def test_concurrent_adds_of_the_same_name_conflict(
        self, uow_factory: UnitOfWorkFactory
    ) -> None:
        async def add(name: str) -> User:
            user = builders.user(name)
            async with uow_factory() as uow:
                await uow.users.add(user)
                await asyncio.sleep(0)
                await uow.commit()
            return user

        results = await asyncio.gather(add("carol"), add("Carol"), return_exceptions=True)
        added = [result for result in results if isinstance(result, User)]
        assert len(added) == 1
        assert any(isinstance(result, ConflictError) for result in results)

    async def test_list_all(self, uow_factory: UnitOfWorkFactory) -> None:
        users = [builders.user() for _ in range(3)]
        await seed(uow_factory, *users)
        async with uow_factory() as uow:
            assert {user.id for user in await uow.users.list_all()} >= {u.id for u in users}

    # --- spaces --------------------------------------------------------------------------------

    async def test_space_round_trip(self, uow_factory: UnitOfWorkFactory) -> None:
        owner, reader, writer = builders.user(), builders.user(), builders.user()
        space = builders.space(owner, "Household")
        space.share(reader.id, ShareLevel.READ)
        space.share(writer.id, ShareLevel.READ_WRITE)
        await seed(uow_factory, owner, reader, writer, spaces=(space,))
        async with uow_factory() as uow:
            assert await uow.spaces.get(space.id) == space

    async def test_space_names_are_unique_per_owner(self, uow_factory: UnitOfWorkFactory) -> None:
        alice, bob = builders.user(), builders.user()
        await seed(
            uow_factory,
            alice,
            bob,
            spaces=(builders.space(alice, "Taxes"), builders.space(bob, "Taxes")),
        )
        with pytest.raises(ConflictError):
            await seed(uow_factory, spaces=(builders.space(alice, "TAXES"),))

    async def test_one_default_space_per_owner(self, uow_factory: UnitOfWorkFactory) -> None:
        owner, _ = await owner_with_space(uow_factory)
        second = Space(
            id=SpaceId(new_id()),
            owner_id=owner.id,
            name="Another default",
            is_default=True,
            created_at=NOW,
        )
        with pytest.raises(ConflictError):
            await seed(uow_factory, spaces=(second,))

    async def test_default_and_accessible_spaces(self, uow_factory: UnitOfWorkFactory) -> None:
        alice, bob, carol = builders.user(), builders.user(), builders.user()
        alice_default, bob_default = builders.default_space(alice), builders.default_space(bob)
        shared = builders.space(alice, "Shared")
        shared.share(bob.id, ShareLevel.READ)
        await seed(uow_factory, alice, bob, carol, spaces=(alice_default, bob_default, shared))
        async with uow_factory() as uow:
            assert await uow.spaces.get_default(alice.id) == alice_default
            with pytest.raises(NotFoundError):
                await uow.spaces.get_default(carol.id)
            accessible = {space.id for space in await uow.spaces.list_accessible(bob.id)}
            assert accessible == {bob_default.id, shared.id}
            assert await uow.spaces.list_accessible(carol.id) == []
            every = {space.id for space in await uow.spaces.list_all()}
            assert every >= {alice_default.id, bob_default.id, shared.id}

    # --- master data ----------------------------------------------------------------------------

    async def test_master_data_round_trip_and_names(self, uow_factory: UnitOfWorkFactory) -> None:
        contact = Contact.create(name="Stadtwerke", now=NOW)
        document_type = DocumentType.create(name="Invoice", now=NOW)
        tag = Tag.create(name="Tax 2026", now=NOW)
        async with uow_factory() as uow:
            await uow.contacts.add(contact)
            await uow.document_types.add(document_type)
            await uow.tags.add(tag)
            await uow.commit()
        async with uow_factory() as uow:
            assert await uow.contacts.get(contact.id) == contact
            assert await uow.document_types.find_by_name("invoice") == document_type
            assert await uow.tags.find_by_name("TAX 2026") == tag
            assert await uow.contacts.find_by_name("Unknown") is None
            # The same name in another kind is fine; in the same kind it is a conflict.
            await uow.tags.add(Tag.create(name="Stadtwerke", now=NOW))
            with pytest.raises(ConflictError):
                await uow.contacts.add(Contact.create(name="stadtwerke", now=NOW))
                await uow.commit()

    async def test_tags_keep_scope_parent_assignment_and_display(
        self, uow_factory: UnitOfWorkFactory
    ) -> None:
        top = Tag.create(name="Insurance", now=NOW)
        child = Tag.create(
            name="Health", now=NOW, parent_id=top.id, assignment=Assignment.MANUAL, shown=False
        )
        group = Tag.create(name="Insurance", now=NOW, scope=TagScope.CONTACT)
        type_group = Tag.create(name="Bills", now=NOW, scope=TagScope.DOCUMENT_TYPE, shown=True)
        async with uow_factory() as uow:
            for item in (top, child, group, type_group):
                await uow.tags.add(item)
            await uow.commit()
        async with uow_factory() as uow:
            assert await uow.tags.get(child.id) == child
            assert await uow.tags.get(type_group.id) == type_group
            assert await uow.tags.find_by_name("INSURANCE") == top
            assert await uow.tags.find_by_name("insurance", TagScope.CONTACT) == group
            assert await uow.tags.find_by_name("Bills") is None
            stored = await uow.tags.get(child.id)
            stored.move(None)
            stored.rename("Health insurance")
            await uow.tags.update(stored)
            await uow.commit()
        async with uow_factory() as uow:
            assert (await uow.tags.get(child.id)).parent_id is None
        # Names are unique per scope.
        with pytest.raises(ConflictError):
            async with uow_factory() as uow:
                await uow.tags.add(Tag.create(name="insurance", now=NOW, scope=TagScope.CONTACT))
                await uow.commit()

    async def test_master_data_keeps_icons_colours_and_logos(
        self, uow_factory: UnitOfWorkFactory
    ) -> None:
        tag = Tag.create(name="Health", now=NOW, icon="heart-pulse", color="pink")
        own = Tag.create(name="Car", now=NOW, scope=TagScope.TOPIC, icon="car", color="#b45309")
        plain = Tag.create(name="Plain", now=NOW)
        kind = DocumentType.create(name="Invoice", now=NOW, icon="receipt-euro")
        uploaded = Contact.create(name="Nord", now=NOW)
        uploaded.set_logo(ContactLogo(source=LogoSource.UPLOADED, stored_at=NOW))
        failed = Contact.create(name="Süd", now=NOW)
        failed.note_failed_fetch(["sued.example", "sued-ag.example"], NOW)
        async with uow_factory() as uow:
            for item in (tag, own, plain):
                await uow.tags.add(item)
            await uow.document_types.add(kind)
            await uow.contacts.add(uploaded)
            await uow.contacts.add(failed)
            await uow.commit()
        async with uow_factory() as uow:
            assert await uow.tags.get(tag.id) == tag
            assert (await uow.tags.get(own.id)).color == "#b45309"
            assert (await uow.tags.get(plain.id)).icon is None
            assert (await uow.document_types.get(kind.id)).icon == "receipt-euro"
            stored = await uow.contacts.get(uploaded.id)
            assert stored.logo == uploaded.logo and stored.logo_failure is None
            other = await uow.contacts.get(failed.id)
            assert other.logo is None and other.logo_failure == FailedLogoFetch(
                domains=frozenset({"sued.example", "sued-ag.example"}), failed_at=NOW
            )
            later = NOW + timedelta(minutes=1)
            other.set_logo(
                ContactLogo(source=LogoSource.FETCHED, stored_at=later, domain="sued.example")
            )
            stored.remove_logo()
            for contact in (stored, other):
                await uow.contacts.update(contact)
            await uow.commit()
        async with uow_factory() as uow:
            assert (await uow.contacts.get(uploaded.id)).logo is None
            fetched = await uow.contacts.get(failed.id)
            assert fetched.logo == ContactLogo(
                source=LogoSource.FETCHED, stored_at=later, domain="sued.example"
            )
            assert fetched.logo_failure is None

    async def test_contacts_and_types_keep_their_groups(
        self, uow_factory: UnitOfWorkFactory
    ) -> None:
        insurer = Tag.create(name="Insurer", now=NOW, scope=TagScope.CONTACT)
        employer = Tag.create(name="Employer", now=NOW, scope=TagScope.CONTACT)
        bills = Tag.create(name="Bills", now=NOW, scope=TagScope.DOCUMENT_TYPE)
        contact = Contact.create(name="Nord", now=NOW)
        contact.set_groups([insurer.id, employer.id])
        kind = DocumentType.create(name="Invoice", now=NOW)
        kind.set_groups([bills.id])
        async with uow_factory() as uow:
            for tag in (insurer, employer, bills):
                await uow.tags.add(tag)
            await uow.contacts.add(contact)
            await uow.document_types.add(kind)
            await uow.commit()
        async with uow_factory() as uow:
            stored = await uow.contacts.get(contact.id)
            assert stored.group_ids == {insurer.id, employer.id}
            assert (await uow.document_types.get(kind.id)).group_ids == {bills.id}
            stored.set_groups([employer.id])
            await uow.contacts.update(stored)
            await uow.commit()
        async with uow_factory() as uow:
            assert (await uow.contacts.get(contact.id)).group_ids == {employer.id}
            await uow.contacts.remove(contact.id)
            await uow.document_types.remove(kind.id)
            await uow.commit()
        async with uow_factory() as uow:
            # The groups stay; what was in them went with the contact and the type.
            assert {tag.id for tag in await uow.tags.list_all()} >= {insurer.id, bills.id}
            await uow.tags.remove(employer.id)
            await uow.commit()

    async def test_topics_at_contacts_identifiers_and_documents(
        self, uow_factory: UnitOfWorkFactory
    ) -> None:
        """A contact keeps the topics it enables and the topic of each identifier; a document
        keeps its topic, is found by it (one of a set) and counted by it."""
        owner, space = await owner_with_space(uow_factory)
        stranger = builders.user()
        await seed(uow_factory, stranger)
        insurance = Tag.create(name="Insurance", now=NOW, scope=TagScope.TOPIC)
        health = Tag.create(name="Health", now=NOW, scope=TagScope.TOPIC, parent_id=insurance.id)
        work = Tag.create(name="Work", now=NOW, scope=TagScope.TOPIC)
        number = Identifier.parse(IdentifierKind.PERSONNEL_NUMBER, "4711", topic_id=work.id)
        policy = Identifier.parse(IdentifierKind.CUSTOMER_NUMBER, "KV-2026-1")
        contact = Contact.create(
            name="Nord", now=NOW, topic_ids=[work.id, health.id], identifiers=[number, policy]
        )
        contact.set_learned(
            [Identifier(IdentifierKind.EMAIL_DOMAIN, "nord.example", topic_id=health.id)]
        )
        documents = []
        for topic in (work.id, health.id, None):
            document = builders.processed(owner, space)
            document.apply_changes(DocumentChanges(contact_id=contact.id, topic_id=topic), {}, NOW)
            document.mark_confirmed(NOW)
            documents.append(document)
        async with uow_factory() as uow:
            for tag in (insurance, health, work):
                await uow.tags.add(tag)
            await uow.contacts.add(contact)
            for document in documents:
                await uow.documents.add(document)
            await uow.commit()
        numbers = {document.id: index for index, document in enumerate(documents)}
        async with uow_factory() as uow:
            stored = await uow.contacts.get(contact.id)
            assert stored == contact
            assert stored.topic_ids == {work.id, health.id}
            assert [item.topic_id for item in stored.identifiers] == [None, work.id, health.id]
            assert (await uow.documents.get(documents[0].id)).topic_id == work.id

            async def found(topics: frozenset[TagId]) -> set[int]:
                filter = DocumentFilter(topics=topics)
                listed = await uow.documents.query(filter, limit=9)
                assert listed == await uow.documents.query_visible(owner.id, filter, limit=9)
                return {numbers[document.id] for document in listed}

            assert await found(frozenset({work.id})) == {0}
            assert await found(frozenset({work.id, health.id})) == {0, 1}
            assert await found(frozenset({insurance.id})) == set()
            assert await uow.documents.count_by(CountBy.TOPIC, None) == {work.id: 1, health.id: 1}
            assert await uow.documents.count_by(CountBy.TOPIC, stranger.id) == {}
            assert (await uow.documents.confirmed_labels(owner.id)).topics == {work.id, health.id}
            assert await uow.documents.exists(topic=work.id)
            assert await uow.documents.exists(contact=contact.id, topic=health.id)
            assert not await uow.documents.exists(topic=insurance.id)
            # Taking a topic away leaves its identifiers without one.
            stored.set_topics([work.id])
            await uow.contacts.update(stored)
            await uow.commit()
        async with uow_factory() as uow:
            stored = await uow.contacts.get(contact.id)
            assert stored.topic_ids == {work.id}
            assert [item.topic_id for item in stored.identifiers] == [None, work.id, None]
            for document in documents:
                await uow.documents.remove(document.id)
            await uow.contacts.remove(contact.id)
            await uow.commit()
        async with uow_factory() as uow:
            # The topics stay; the contact took its enabling with it.
            await uow.tags.remove(work.id)
            await uow.commit()

    async def test_contact_aliases_and_type_description(
        self, uow_factory: UnitOfWorkFactory
    ) -> None:
        """Aliases keep their order, change with the contact and go with it; an alias is unique
        across contacts. A document type keeps its description."""
        example = Contact.create(
            name="Nord Versicherungsgruppe",
            now=NOW,
            aliases=["Nord Krankenversicherung AG", "Nord Lebensversicherung AG"],
        )
        pay = DocumentType.create(name="Pay slip", now=NOW, description="Entgeltbescheinigung")
        async with uow_factory() as uow:
            await uow.contacts.add(example)
            await uow.document_types.add(pay)
            await uow.commit()
        async with uow_factory() as uow:
            assert await uow.contacts.get(example.id) == example
            assert (await uow.document_types.get(pay.id)).description == "Entgeltbescheinigung"
            stored = await uow.contacts.get(example.id)
            stored.set_aliases(["Nord Lebensversicherung AG", "Nord Allgemeine"])
            await uow.contacts.update(stored)
            await uow.commit()
        async with uow_factory() as uow:
            assert (await uow.contacts.get(example.id)).aliases == [
                "Nord Lebensversicherung AG",
                "Nord Allgemeine",
            ]
            with pytest.raises(ConflictError):
                await uow.contacts.add(
                    Contact.create(name="Other", now=NOW, aliases=["nord allgemeine"])
                )
                await uow.commit()
        other = Contact.create(name="Other", now=NOW)
        async with uow_factory() as uow:
            await uow.contacts.add(other)
            await uow.commit()
        async with uow_factory() as uow:
            # An alias moves to another contact in one transaction.
            source, target = await uow.contacts.get(example.id), await uow.contacts.get(other.id)
            source.set_aliases(["Nord Lebensversicherung AG"])
            target.set_aliases(["Nord Allgemeine"])
            await uow.contacts.update(source)
            await uow.contacts.update(target)
            await uow.commit()
        async with uow_factory() as uow:
            assert (await uow.contacts.get(other.id)).aliases == ["Nord Allgemeine"]
            await uow.contacts.remove(example.id)
            await uow.contacts.remove(other.id)
            await uow.commit()
        async with uow_factory() as uow:
            # The aliases went with the contact: they are free again.
            await uow.contacts.add(Contact.create(name="New", now=NOW, aliases=["Nord Allgemeine"]))
            await uow.commit()

    async def test_contact_identifiers(self, uow_factory: UnitOfWorkFactory) -> None:
        """Identifiers keep their source, change with the contact and go with it; kind and
        value are unique across contacts, also when rejected."""
        iban = Identifier.parse(IdentifierKind.IBAN, "DE89 3704 0044 0532 0130 00")
        domain = Identifier.parse(IdentifierKind.EMAIL_DOMAIN, "stadtwerke.example")
        contact = Contact.create(name="Stadtwerke", now=NOW, identifiers=[iban])
        contact.set_learned([domain])
        async with uow_factory() as uow:
            await uow.contacts.add(contact)
            await uow.commit()
        async with uow_factory() as uow:
            stored = await uow.contacts.get(contact.id)
            assert stored == contact
            assert [item.source for item in stored.identifiers] == [
                IdentifierSource.MANUAL,
                IdentifierSource.LEARNED,
            ]
            stored.set_identifiers([iban], rejected=[domain])
            await uow.contacts.update(stored)
            await uow.commit()
        async with uow_factory() as uow:
            stored = await uow.contacts.get(contact.id)
            assert stored.identifiers[1] == Identifier(
                IdentifierKind.EMAIL_DOMAIN, "stadtwerke.example", IdentifierSource.REJECTED
            )
            with pytest.raises(ConflictError):
                await uow.contacts.add(Contact.create(name="Other", now=NOW, identifiers=[domain]))
                await uow.commit()
        other = Contact.create(name="Other", now=NOW)
        async with uow_factory() as uow:
            await uow.contacts.add(other)
            await uow.commit()
        async with uow_factory() as uow:
            # A value moves to another contact in one transaction, given up first.
            source, target = await uow.contacts.get(contact.id), await uow.contacts.get(other.id)
            source.set_identifiers([])
            target.set_identifiers([iban])
            await uow.contacts.update(source)
            await uow.contacts.update(target)
            await uow.commit()
        async with uow_factory() as uow:
            assert (await uow.contacts.get(other.id)).identifiers == [iban]
            await uow.contacts.remove(contact.id)
            await uow.contacts.remove(other.id)
            await uow.commit()
        async with uow_factory() as uow:
            await uow.contacts.add(Contact.create(name="New", now=NOW, identifiers=[iban, domain]))
            await uow.commit()

    async def test_field_definition_round_trip(self, uow_factory: UnitOfWorkFactory) -> None:
        """Scope and choices; a field required for some of its types or, global, everywhere.
        A document type keeps its title pattern."""
        invoice = DocumentType.create(
            name="Invoice", now=NOW, title_pattern="{document_date:YYYY} {field:Number}"
        )
        reminder = DocumentType.create(name="Reminder", now=NOW)
        bound = FieldDefinition.create(
            name="Billing period",
            data_type=FieldType.CHOICE,
            now=NOW,
            document_type_ids=[invoice.id, reminder.id],
            choices=["monthly", "yearly"],
            required_types=[invoice.id],
        )
        global_ = FieldDefinition.create(
            name="Note", data_type=FieldType.TEXT, now=NOW, required=True
        )
        async with uow_factory() as uow:
            await uow.document_types.add(invoice)
            await uow.document_types.add(reminder)
            await uow.fields.add(bound)
            await uow.fields.add(global_)
            await uow.commit()
        async with uow_factory() as uow:
            assert await uow.fields.get(bound.id) == bound
            assert await uow.fields.get(global_.id) == global_
            assert await uow.fields.find_by_name("NOTE") == global_
            assert (await uow.document_types.get(invoice.id)).title_pattern == (
                "{document_date:YYYY} {field:Number}"
            )
            stored = await uow.fields.get(bound.id)
            assert stored.is_required_for(invoice.id)
            assert not stored.is_required_for(reminder.id)

    async def test_a_field_keeps_its_description(self, uow_factory: UnitOfWorkFactory) -> None:
        gross = FieldDefinition.create(
            name="LA_Brutto",
            data_type=FieldType.AMOUNT,
            now=NOW,
            description="Gesamtbrutto des Monats, nicht das Grundgehalt",
        )
        async with uow_factory() as uow:
            await uow.fields.add(gross)
            await uow.commit()
        async with uow_factory() as uow:
            stored = await uow.fields.get(gross.id)
            assert stored.description == "Gesamtbrutto des Monats, nicht das Grundgehalt"
            stored.describe("  ")
            await uow.fields.update(stored)
            await uow.commit()
        async with uow_factory() as uow:
            assert (await uow.fields.get(gross.id)).description is None

    async def test_a_field_keeps_whether_it_is_set_by_hand(
        self, uow_factory: UnitOfWorkFactory
    ) -> None:
        asn = FieldDefinition.create(
            name="ASN", data_type=FieldType.NUMBER, now=NOW, assignment=Assignment.MANUAL
        )
        async with uow_factory() as uow:
            await uow.fields.add(asn)
            await uow.commit()
        async with uow_factory() as uow:
            stored = await uow.fields.get(asn.id)
            assert stored.assignment is Assignment.MANUAL and not stored.is_read
            stored.assignment = Assignment.AUTO
            await uow.fields.update(stored)
            await uow.commit()
        async with uow_factory() as uow:
            assert (await uow.fields.get(asn.id)).is_read

    # --- documents ------------------------------------------------------------------------------

    async def test_document_round_trip_with_all_fields(
        self, uow_factory: UnitOfWorkFactory
    ) -> None:
        owner, space = await owner_with_space(uow_factory)
        contact = Contact.create(name="Bank", now=NOW)
        invoice = DocumentType.create(name="Invoice", now=NOW)
        tags = [Tag.create(name=name, now=NOW) for name in ("a", "b")]
        definitions = [
            FieldDefinition.create(
                name=f"field {data_type}",
                data_type=data_type,
                now=NOW,
                choices=["x", "y"] if data_type is FieldType.CHOICE else (),
            )
            for data_type in FieldType
        ]
        values: dict[FieldType, object] = {
            FieldType.TEXT: "Contract 7",
            FieldType.NUMBER: Decimal("1234.5678"),
            FieldType.AMOUNT: Money(Decimal("-12.30"), "CHF"),
            FieldType.DATE: date(2026, 2, 28),
            FieldType.BOOLEAN: False,
            FieldType.CHOICE: "y",
            FieldType.LINK: Url("https://example.org/contract?id=7"),
            FieldType.PERIOD: Period(date(2026, 9, 1), date(2026, 9, 30)),
        }
        document = builders.document(owner, space)
        document.apply_changes(
            DocumentChanges(
                title="Statement",
                contact_id=contact.id,
                document_type_id=invoice.id,
                tag_ids=frozenset(tag.id for tag in tags),
                document_date=date(2026, 3, 1),
                fields={d.id: values[d.data_type] for d in definitions},
            ),
            {d.id: d for d in definitions},
            NOW,
        )
        builders.run_pipeline(document, {Node.JUDGE: UNCERTAIN})
        document.pull_events()
        async with uow_factory() as uow:
            await uow.contacts.add(contact)
            await uow.document_types.add(invoice)
            for tag in tags:
                await uow.tags.add(tag)
            for definition in definitions:
                await uow.fields.add(definition)
            await uow.documents.add(document)
            await uow.commit()
        async with uow_factory() as uow:
            assert await uow.documents.get(document.id) == document

    async def test_document_processing_state_round_trip(
        self, uow_factory: UnitOfWorkFactory
    ) -> None:
        owner, space = await owner_with_space(uow_factory)
        failed = builders.processed(owner, space, {Node.PARSE: FAILED})
        failed.retry(NOW)
        failed.pull_events()
        received = builders.document(owner, space)
        in_review = builders.processed(owner, space, {Node.JUDGE: UNCERTAIN})
        assert in_review.processing.status is ProcessingStatus.REVIEW
        # A person asked that the vision language model read it.
        read_again = builders.processed(owner, space, {})
        read_again.read_with_vision(NOW)
        read_again.pull_events()
        assert read_again.processing.vision
        documents = (failed, received, in_review, read_again)
        async with uow_factory() as uow:
            for document in documents:
                await uow.documents.add(document)
            await uow.commit()
        async with uow_factory() as uow:
            for document in documents:
                assert await uow.documents.get(document.id) == document

    async def test_loaded_documents_carry_no_events(self, uow_factory: UnitOfWorkFactory) -> None:
        owner, space = await owner_with_space(uow_factory)
        document = builders.document(owner, space)
        builders.run_pipeline(document)  # events recorded but not pulled
        async with uow_factory() as uow:
            await uow.documents.add(document)
            await uow.commit()
        async with uow_factory() as uow:
            assert (await uow.documents.get(document.id)).pull_events() == []

    async def test_original_is_unique_per_owner(self, uow_factory: UnitOfWorkFactory) -> None:
        alice, alice_space = await owner_with_space(uow_factory)
        bob, bob_space = await owner_with_space(uow_factory)
        first = builders.document(alice, alice_space, content="same")
        async with uow_factory() as uow:
            await uow.documents.add(first)
            await uow.documents.add(builders.document(bob, bob_space, content="same"))
            await uow.commit()
        async with uow_factory() as uow:
            assert await uow.documents.find_by_sha256(alice.id, first.sha256) == first
            assert await uow.documents.find_by_sha256(alice.id, builders.sha256()) is None
        with pytest.raises(ConflictError):
            async with uow_factory() as uow:
                await uow.documents.add(builders.document(alice, alice_space, content="same"))
                await uow.commit()

    async def test_document_identifiers_tell_the_contacts_that_show_them(
        self, uow_factory: UnitOfWorkFactory
    ) -> None:
        """Only confirmed documents with a contact count."""
        owner, space = await owner_with_space(uow_factory)
        utility, bank = (Contact.create(name=name, now=NOW) for name in ("Stadtwerke", "Bank"))
        own = (IdentifierKind.IBAN, "DE02120300000000202051")
        customer = (IdentifierKind.CUSTOMER_NUMBER, "20047718")
        other = (IdentifierKind.VAT_ID, "DE312456789")
        documents = [builders.processed(owner, space) for _ in range(4)]
        shows = [{own, customer}, {own}, {other}, {own}]
        for document, contact in zip(documents, [utility, bank, utility, None], strict=True):
            document.contact_id = None if contact is None else contact.id
        for document in (documents[0], documents[1], documents[3]):
            document.mark_confirmed(NOW)
        async with uow_factory() as uow:
            await uow.contacts.add(utility)
            await uow.contacts.add(bank)
            for document, identifiers in zip(documents, shows, strict=True):
                await uow.documents.add(document)
                await uow.document_identifiers.replace(document.id, identifiers)
            await uow.commit()
        unknown = (IdentifierKind.EMAIL_DOMAIN, "example.org")
        async with uow_factory() as uow:
            assert await uow.document_identifiers.contacts_of([own, customer, other, unknown]) == {
                own: frozenset({utility.id, bank.id}),
                customer: frozenset({utility.id}),
            }
            assert await uow.document_identifiers.contacts_of([]) == {}
            await uow.document_identifiers.replace(documents[0].id, [customer])
            await uow.documents.remove(documents[1].id)
            await uow.commit()
        async with uow_factory() as uow:
            assert await uow.document_identifiers.contacts_of([own, customer]) == {
                customer: frozenset({utility.id})
            }

    async def test_remove_document(self, uow_factory: UnitOfWorkFactory) -> None:
        owner, space = await owner_with_space(uow_factory)
        document = builders.document(owner, space)
        async with uow_factory() as uow:
            await uow.documents.add(document)
            await uow.commit()
        async with uow_factory() as uow:
            await uow.documents.remove(document.id)
            assert await uow.documents.find(document.id) is None
            with pytest.raises(NotFoundError):
                await uow.documents.remove(document.id)
            await uow.commit()
        async with uow_factory() as uow:
            assert await uow.documents.find(document.id) is None

    async def test_visible_documents_follow_the_permission_rules(
        self, uow_factory: UnitOfWorkFactory
    ) -> None:
        owner, reader, stranger = builders.user(), builders.user(), builders.user()
        private = builders.default_space(owner)
        shared = builders.space(owner, "Shared")
        shared.share(reader.id, ShareLevel.READ)
        reader_default = builders.default_space(reader)
        await seed(uow_factory, owner, reader, stranger, spaces=(private, shared, reader_default))
        documents = [
            builders.processed(owner, private),
            builders.processed(owner, shared),
            builders.processed(owner, shared, {Node.JUDGE: UNCERTAIN}),
            builders.processed(owner, shared, {Node.OCR: FAILED}),
            builders.document(owner, shared),
            builders.processed(reader, shared),
            builders.processed(reader, shared, {Node.JUDGE: UNCERTAIN}),
            builders.processed(reader, reader_default),
        ]
        async with uow_factory() as uow:
            for document in documents:
                await uow.documents.add(document)
            await uow.commit()
        spaces = {space.id: space for space in (private, shared, reader_default)}
        async with uow_factory() as uow:
            for user in (owner, reader, stranger):
                expected = {
                    document.id
                    for document in documents
                    if can_read_document(user, document, spaces[document.space_id])
                }
                visible = {document.id for document in await uow.documents.list_visible_to(user.id)}
                assert visible == expected, user.username
                queried = await uow.documents.query_visible(user.id, DocumentFilter(), limit=100)
                assert {document.id for document in queried} == expected, user.username
                for lane in (Lane.YELLOW, Lane.RED, None):
                    only = DocumentFilter(lanes=frozenset({lane}), space=shared.id)
                    found = await uow.documents.query_visible(user.id, only, limit=100)
                    assert {d.id for d in found} <= expected, user.username
        assert len(expected) == 0  # the stranger sees nothing
        async with uow_factory() as uow:
            every = await uow.documents.query(DocumentFilter(), limit=100)
            assert {d.id for d in every} >= {d.id for d in documents}
            yellow = DocumentFilter(lanes=frozenset({Lane.YELLOW}), space=shared.id)
            yellow_ids = {d.id for d in await uow.documents.query(yellow, limit=100)}
            assert yellow_ids == {d.id for d in documents if d.lane is Lane.YELLOW}

    async def test_confirmed_documents(self, uow_factory: UnitOfWorkFactory) -> None:
        """The time a person confirmed the metadata is kept; documents filter and count by it."""
        owner, space = await owner_with_space(uow_factory)
        early, late, never = (builders.processed(owner, space) for _ in range(3))
        early.mark_confirmed(NOW)
        late.mark_confirmed(NOW + timedelta(hours=1))
        async with uow_factory() as uow:
            for document in (early, late, never):
                await uow.documents.add(document)
            await uow.commit()
        async with uow_factory() as uow:
            assert (await uow.documents.get(late.id)).confirmed_at == NOW + timedelta(hours=1)
            assert (await uow.documents.get(never.id)).confirmed_at is None
            confirmed = await uow.documents.query(DocumentFilter(confirmed=True), limit=9)
            assert {document.id for document in confirmed} == {early.id, late.id}
            open_ = await uow.documents.query(DocumentFilter(confirmed=False), limit=9)
            assert [document.id for document in open_] == [never.id]
            assert await uow.documents.count_confirmed() == 2
            assert await uow.documents.count_confirmed(after=NOW) == 1

    async def test_confirmed_labels(self, uow_factory: UnitOfWorkFactory) -> None:
        """What the confirmed documents a user may read carry; not what the open ones do, nor
        another owner's."""
        owner, space = await owner_with_space(uow_factory)
        stranger, elsewhere = await owner_with_space(uow_factory)
        known, other, foreign = (
            Contact.create(name=name, now=NOW) for name in ("Known", "Other", "Foreign")
        )
        kind = DocumentType.create(name="Invoice", now=NOW)
        tag, untouched = (Tag.create(name=name, now=NOW) for name in ("Energy", "Unused"))
        confirmed, open_ = builders.processed(owner, space), builders.processed(owner, space)
        theirs = builders.processed(stranger, elsewhere)
        confirmed.apply_changes(
            DocumentChanges(
                contact_id=known.id, document_type_id=kind.id, tag_ids=frozenset({tag.id})
            ),
            {},
            NOW,
        )
        open_.apply_changes(
            DocumentChanges(contact_id=other.id, tag_ids=frozenset({untouched.id})), {}, NOW
        )
        theirs.apply_changes(DocumentChanges(contact_id=foreign.id), {}, NOW)
        for document in (confirmed, theirs):
            document.mark_confirmed(NOW)
        async with uow_factory() as uow:
            for contact in (known, other, foreign):
                await uow.contacts.add(contact)
            await uow.document_types.add(kind)
            for label in (tag, untouched):
                await uow.tags.add(label)
            for document in (confirmed, open_, theirs):
                await uow.documents.add(document)
            await uow.commit()
        async with uow_factory() as uow:
            assert await uow.documents.confirmed_labels(owner.id) == KnownLabels(
                contacts=frozenset({known.id}),
                document_types=frozenset({kind.id}),
                tags=frozenset({tag.id}),
            )
            assert await uow.documents.confirmed_labels(stranger.id) == KnownLabels(
                contacts=frozenset({foreign.id})
            )

    async def test_classifier_models(self, uow_factory: UnitOfWorkFactory) -> None:
        """Newest first by number; a number is used once."""
        first, second = (
            ClassifierModel.create(
                number=number,
                now=NOW,
                examples=40 * number,
                examples_until=None if number == 1 else NOW,
                object_key=f"classifier/model-{number}.npz",
                summary={"classes": {"contact": number}},
            )
            for number in (1, 2)
        )
        async with uow_factory() as uow:
            assert await uow.classifier_models.latest() is None
            await uow.classifier_models.add(first)
            await uow.classifier_models.add(second)
            await uow.commit()
        async with uow_factory() as uow:
            assert await uow.classifier_models.latest() == second
            assert await uow.classifier_models.list_all() == [second, first]
            await uow.classifier_models.remove(first.id)
            await uow.commit()
        async with uow_factory() as uow:
            assert await uow.classifier_models.list_all() == [second]
            with pytest.raises(ConflictError):
                again = ClassifierModel.create(
                    number=2, now=NOW, examples=1, examples_until=None, object_key="x", summary={}
                )
                await uow.classifier_models.add(again)
                await uow.commit()

    async def test_query_filters_and_pages(self, uow_factory: UnitOfWorkFactory) -> None:
        owner, space = await owner_with_space(uow_factory)
        other = builders.space(owner, "Other")
        await seed(uow_factory, spaces=(other,))
        contact = Contact.create(name="ACME", now=NOW)
        invoice = DocumentType.create(name="Invoice", now=NOW)
        tax, paid = Tag.create(name="tax", now=NOW), Tag.create(name="paid", now=NOW)
        documents = []
        for number in range(6):
            document = builders.processed(
                owner,
                other if number == 5 else space,
                {Node.JUDGE: UNCERTAIN} if number == 4 else None,
            )
            tags = frozenset({tax.id, paid.id} if number < 2 else {tax.id} if number < 4 else set())
            document.apply_changes(
                DocumentChanges(
                    contact_id=contact.id if number % 2 == 0 else None,
                    document_type_id=invoice.id if number < 3 else None,
                    tag_ids=tags,
                ),
                {},
                NOW,
            )
            documents.append(document)
        processing = builders.document(owner, space)
        async with uow_factory() as uow:
            for item in (contact, invoice, tax, paid):
                await getattr(uow, _repository_of(item)).add(item)
            for document in [*documents, processing]:
                await uow.documents.add(document)
            await uow.commit()

        numbers = {document.id: number for number, document in enumerate(documents)}

        def ids(found: list[Document]) -> list[int]:
            return [numbers.get(document.id, -1) for document in found]

        async with uow_factory() as uow:
            query = uow.documents.query_visible
            everything = await query(owner.id, DocumentFilter(), limit=100)
            all_ids = [d.id for d in [*documents, processing]]
            assert [d.id for d in everything] == sorted(all_ids, reverse=True)
            assert set(ids(await query(owner.id, DocumentFilter(contact=contact.id), limit=9))) == {
                0,
                2,
                4,
            }
            by_type = DocumentFilter(document_type=invoice.id)
            assert set(ids(await query(owner.id, by_type, limit=9))) == {0, 1, 2}
            both_tags = DocumentFilter(tags=(frozenset({tax.id}), frozenset({paid.id})))
            assert set(ids(await query(owner.id, both_tags, limit=9))) == {0, 1}
            either_tag = DocumentFilter(tags=(frozenset({tax.id, paid.id}),))
            assert set(ids(await query(owner.id, either_tag, limit=9))) == {0, 1, 2, 3}
            assert set(ids(await query(owner.id, DocumentFilter(space=other.id), limit=9))) == {5}
            yellow = DocumentFilter(lanes=frozenset({Lane.YELLOW}))
            assert set(ids(await query(owner.id, yellow, limit=9))) == {4}
            in_processing = await query(owner.id, DocumentFilter(lanes=frozenset({None})), limit=9)
            assert [d.id for d in in_processing] == [processing.id]
            combined = DocumentFilter(
                contact=contact.id, document_type=invoice.id, tags=(frozenset({paid.id}),)
            )
            assert set(ids(await query(owner.id, combined, limit=9))) == {0}

            # Pages of two, following the last id.
            pages: list[DocumentId] = []
            after = None
            while page := await query(owner.id, DocumentFilter(), after=after, limit=2):
                assert len(page) <= 2
                pages += [d.id for d in page]
                after = DocumentPosition(page[-1].id)
            assert pages == [d.id for d in everything]

            # Over every document: the same filters and paging.
            assert [d.id for d in await query(owner.id, both_tags, limit=9)] == [
                d.id for d in await uow.documents.query(both_tags, limit=9)
            ]
            in_space = [d.id for d in everything if d.space_id == space.id]
            first = await uow.documents.query(DocumentFilter(space=space.id), limit=2)
            rest = await uow.documents.query(
                DocumentFilter(space=space.id), after=DocumentPosition(first[-1].id), limit=100
            )
            assert [d.id for d in [*first, *rest]] == in_space

    async def test_query_by_groups_and_counts_of_tag_sets(
        self, uow_factory: UnitOfWorkFactory
    ) -> None:
        owner, space = await owner_with_space(uow_factory)
        stranger = builders.user()
        await seed(uow_factory, stranger)
        insurer = Tag.create(name="Insurer", now=NOW, scope=TagScope.CONTACT)
        employer = Tag.create(name="Employer", now=NOW, scope=TagScope.CONTACT)
        bills = Tag.create(name="Bills", now=NOW, scope=TagScope.DOCUMENT_TYPE)
        tax, paid = Tag.create(name="tax", now=NOW), Tag.create(name="paid", now=NOW)
        nord, sued = Contact.create(name="Nord", now=NOW), Contact.create(name="Süd", now=NOW)
        nord.set_groups([insurer.id, employer.id])
        sued.set_groups([employer.id])
        invoice = DocumentType.create(name="Invoice", now=NOW)
        invoice.set_groups([bills.id])
        documents = []
        for contact, kind, tags in (
            (nord, invoice, {tax.id, paid.id}),
            (sued, None, {tax.id}),
            (None, None, set()),
        ):
            document = builders.processed(owner, space)
            changes = DocumentChanges(
                contact_id=None if contact is None else contact.id,
                document_type_id=None if kind is None else kind.id,
                tag_ids=frozenset(tags),
            )
            document.apply_changes(changes, {}, NOW)
            documents.append(document)
        async with uow_factory() as uow:
            for tag in (insurer, employer, bills, tax, paid):
                await uow.tags.add(tag)
            await uow.contacts.add(nord)
            await uow.contacts.add(sued)
            await uow.document_types.add(invoice)
            for document in documents:
                await uow.documents.add(document)
            await uow.commit()
        numbers = {document.id: number for number, document in enumerate(documents)}

        async with uow_factory() as uow:

            async def found(
                contacts: TagSets = (), types: TagSets = (), tags: TagSets = ()
            ) -> set[int]:
                filter = DocumentFilter(
                    contact_groups=contacts, document_type_groups=types, tags=tags
                )
                listed = await uow.documents.query(filter, limit=9)
                assert listed == await uow.documents.query_visible(owner.id, filter, limit=9)
                return {numbers[document.id] for document in listed}

            assert await found(contacts=(frozenset({insurer.id}),)) == {0}
            assert await found(contacts=(frozenset({employer.id}),)) == {0, 1}
            assert await found(contacts=(frozenset({insurer.id, employer.id}),)) == {0, 1}
            assert await found(contacts=(frozenset({insurer.id}), frozenset({employer.id}))) == {0}
            assert await found(types=(frozenset({bills.id}),)) == {0}
            assert await found(
                contacts=(frozenset({employer.id}),), tags=(frozenset({paid.id}),)
            ) == {0}
            assert await found(types=(frozenset({employer.id}),)) == set()

            either, only_paid, none = (TagId(new_id()) for _ in range(3))
            sets: dict[UUID, Collection[TagId]] = {
                either: {tax.id, paid.id},
                only_paid: {paid.id},
                none: {insurer.id},
            }
            assert await uow.documents.count_tagged(sets, None) == {either: 2, only_paid: 1}
            assert await uow.documents.count_tagged(sets, owner.id) == {either: 2, only_paid: 1}
            assert await uow.documents.count_tagged(sets, stranger.id) == {}

    async def test_query_sorts_by_date_and_title_and_filters_by_field(
        self, uow_factory: UnitOfWorkFactory
    ) -> None:
        owner, space = await owner_with_space(uow_factory)
        note = FieldDefinition.create(name="Note", data_type=FieldType.TEXT, now=NOW)
        rows = [  # title, document date, note; in the order they come in
            ("Ärztebrief", date(2026, 3, 1), "a"),
            ("arzt", date(2026, 3, 1), None),
            ("Zahnarzt", None, "z"),
            ("bank", date(2025, 12, 24), None),
            ("Abrechnung", None, None),
        ]
        documents = []
        for title, day, value in rows:
            document = builders.processed(owner, space)
            values = {note.id: value} if value else {}
            changes = DocumentChanges(title=title, document_date=day, fields=values)
            document.apply_changes(changes, {note.id: note}, NOW)
            documents.append(document)
        async with uow_factory() as uow:
            await uow.fields.add(note)
            for document in documents:
                await uow.documents.add(document)
            await uow.commit()
        titles = {document.id: document.title for document in documents}

        async def paged(order: DocumentOrder, filter: DocumentFilter | None = None) -> list[str]:
            found: list[Document] = []
            after = None
            async with uow_factory() as uow:
                while page := await uow.documents.query_visible(
                    owner.id, filter or DocumentFilter(), order=order, after=after, limit=2
                ):
                    found += page
                    after = DocumentPosition.of(page[-1], order)
            return [titles[document.id] for document in found]

        def by_id(*names: str, descending: bool = False) -> list[str]:
            ids = {title: id for id, title in titles.items()}
            return sorted(names, key=lambda name: ids[name], reverse=descending)

        # By date, ties by id the same way; without a date last in both directions.
        newest = await paged(DocumentOrder(SortKey.DOCUMENT_DATE))
        assert newest == [
            *by_id("arzt", "Ärztebrief", descending=True),
            "bank",
            *by_id("Abrechnung", "Zahnarzt", descending=True),
        ], newest
        oldest = await paged(DocumentOrder(SortKey.DOCUMENT_DATE, ascending=True))
        assert oldest == [
            "bank",
            *by_id("arzt", "Ärztebrief"),
            *by_id("Abrechnung", "Zahnarzt"),
        ], oldest
        # By title: case and accents aside.
        by_title = ["Abrechnung", "arzt", "Ärztebrief", "bank", "Zahnarzt"]
        assert await paged(DocumentOrder(SortKey.TITLE, ascending=True)) == by_title
        assert await paged(DocumentOrder(SortKey.TITLE)) == by_title[::-1]
        assert await paged(DocumentOrder(SortKey.CREATED, ascending=True)) == by_id(
            *(title for title, _, _ in rows)
        )
        # Documents with a value for the field.
        with_note = DocumentFilter(field=note.id)
        assert await paged(NEWEST_FIRST, with_note) == by_id(
            "Zahnarzt", "Ärztebrief", descending=True
        )
        async with uow_factory() as uow:
            assert len(await uow.documents.query(with_note, limit=9)) == 2

    async def test_documents_are_counted_by_master_data(
        self, uow_factory: UnitOfWorkFactory
    ) -> None:
        owner, space = await owner_with_space(uow_factory)
        stranger, theirs = await owner_with_space(uow_factory)
        contact = Contact.create(name="ACME", now=NOW)
        invoice = DocumentType.create(name="Invoice", now=NOW)
        tax = Tag.create(name="tax", now=NOW)
        note = FieldDefinition.create(name="Note", data_type=FieldType.TEXT, now=NOW)
        documents = []
        for number, (user, place) in enumerate(
            [(owner, space), (owner, space), (stranger, theirs)]
        ):
            document = builders.processed(user, place)
            changes = DocumentChanges(
                contact_id=contact.id,
                document_type_id=invoice.id if number == 0 else None,
                tag_ids=frozenset({tax.id}),
                fields={note.id: "x"} if number != 1 else {},
            )
            document.apply_changes(changes, {note.id: note}, NOW)
            documents.append(document)
        async with uow_factory() as uow:
            for item in (contact, invoice, tax):
                await getattr(uow, _repository_of(item)).add(item)
            await uow.fields.add(note)
            for document in documents:
                await uow.documents.add(document)
            await uow.commit()

        async with uow_factory() as uow:
            count = uow.documents.count_by
            # Within the owner's reach: their own two (the stranger's space is not shared).
            assert await count(CountBy.CONTACT, owner.id) == {contact.id: 2}
            assert await count(CountBy.DOCUMENT_TYPE, owner.id) == {invoice.id: 1}
            assert await count(CountBy.TAG, owner.id) == {tax.id: 2}
            assert await count(CountBy.FIELD, owner.id) == {note.id: 1}
            # Every document.
            assert await count(CountBy.CONTACT, None) == {contact.id: 3}
            assert await count(CountBy.FIELD, None) == {note.id: 2}

    async def test_removing_a_document_removes_its_processing_log(
        self, uow_factory: UnitOfWorkFactory
    ) -> None:
        owner, space = await owner_with_space(uow_factory)
        document = builders.document(owner, space)
        entry = NodeRun(
            document_id=document.id,
            node=Node.RECEIVE,
            run=1,
            result=OK,
            pipeline_version="0.1.0",
            started_at=NOW,
            duration=timedelta(0),
        )
        async with uow_factory() as uow:
            await uow.documents.add(document)
            await uow.processing_log.append(entry)
            await uow.commit()
        async with uow_factory() as uow:
            await uow.documents.remove(document.id)
            assert await uow.processing_log.list_for(document.id) == []
            await uow.commit()
        async with uow_factory() as uow:
            assert await uow.processing_log.list_for(document.id) == []

    # --- processing log -------------------------------------------------------------------------

    async def test_processing_log(self, uow_factory: UnitOfWorkFactory) -> None:
        owner, space = await owner_with_space(uow_factory)
        document, other = builders.document(owner, space), builders.document(owner, space)
        entries = [
            NodeRun(
                document_id=document.id,
                node=node,
                run=1,
                result=result,
                pipeline_version="0.1.0",
                started_at=NOW,
                duration=timedelta(milliseconds=250),
            )
            for node, result in [
                (Node.RECEIVE, OK),
                (
                    Node.OCR,
                    NodeResult(
                        outcome=Outcome.UNCERTAIN,
                        reason="low quality scan",
                        confidence=0.25,
                        model_version="tesseract 5.5",
                        input={"pages": 2},
                        output={"languages": ["deu", "eng"], "ratio": 0.5, "ok": None},
                    ),
                ),
                (Node.PARSE, FAILED),
            ]
        ]
        async with uow_factory() as uow:
            await uow.documents.add(document)
            await uow.documents.add(other)
            for entry in entries:
                await uow.processing_log.append(entry)
            assert await uow.processing_log.list_for(document.id) == entries
            await uow.commit()
        async with uow_factory() as uow:
            assert await uow.processing_log.list_for(document.id) == entries
            assert await uow.processing_log.list_for(other.id) == []
            await uow.processing_log.append(entries[0])
        async with uow_factory() as uow:
            assert len(await uow.processing_log.list_for(document.id)) == 3

    # --- removal and references -----------------------------------------------------------------

    async def test_users_keep_their_active_flag(self, uow_factory: UnitOfWorkFactory) -> None:
        user = builders.user()
        user.active = False
        await seed(uow_factory, user)
        async with uow_factory() as uow:
            stored = await uow.users.get(user.id)
            assert stored.active is False
            stored.active = True
            await uow.users.update(stored)
            await uow.commit()
        async with uow_factory() as uow:
            assert (await uow.users.get(user.id)).active is True

    async def test_users_and_spaces_are_removed(self, uow_factory: UnitOfWorkFactory) -> None:
        owner, space = await owner_with_space(uow_factory)
        other = builders.user()
        shared = builders.space(owner)
        shared.share(other.id, ShareLevel.READ)
        await seed(uow_factory, other, spaces=(shared,))
        async with uow_factory() as uow:
            await uow.spaces.remove(shared.id)
            await uow.spaces.remove(space.id)
            await uow.users.remove(owner.id)
            await uow.commit()
        async with uow_factory() as uow:
            assert await uow.users.find(owner.id) is None
            assert await uow.spaces.find(shared.id) is None
            assert await uow.spaces.list_accessible(other.id) == []
            with pytest.raises(NotFoundError):
                await uow.users.remove(owner.id)
            with pytest.raises(NotFoundError):
                await uow.spaces.remove(space.id)

    async def test_a_lock_makes_other_units_wait(self, uow_factory: UnitOfWorkFactory) -> None:
        order: list[str] = []
        locked = asyncio.Event()

        async def first() -> None:
            async with uow_factory() as uow:
                await uow.lock("originals/abc")
                locked.set()
                await asyncio.sleep(0.2)
                order.append("first ends")
                await uow.commit()

        async def second() -> None:
            await locked.wait()
            async with uow_factory() as uow:
                await uow.lock("originals/abc")
                order.append("second holds the lock")
                await uow.commit()

        await asyncio.gather(first(), second())
        assert order == ["first ends", "second holds the lock"]

    async def test_a_lock_ends_with_a_rollback(self, uow_factory: UnitOfWorkFactory) -> None:
        async with uow_factory() as uow:
            await uow.lock("originals/abc")
            await uow.rollback()
        with pytest.raises(LookupError):
            async with uow_factory() as uow:
                await uow.lock("originals/abc")
                raise LookupError
        async with asyncio.timeout(5), uow_factory() as uow:
            await uow.lock("originals/abc")
            await uow.commit()

    async def test_field_values_in_use(self, uow_factory: UnitOfWorkFactory) -> None:
        owner, space = await owner_with_space(uow_factory)
        invoice = DocumentType.create(name="Invoice", now=NOW)
        letter = DocumentType.create(name="Letter", now=NOW)
        kind = FieldDefinition.create(
            name="Kind", data_type=FieldType.CHOICE, now=NOW, choices=["a", "b", "c"]
        )
        typed, untyped = builders.document(owner, space), builders.document(owner, space)
        definitions = {kind.id: kind}
        typed.apply_changes(
            DocumentChanges(document_type_id=invoice.id, fields={kind.id: "a"}),
            definitions,
            NOW,
        )
        untyped.apply_changes(DocumentChanges(fields={kind.id: "b"}), definitions, NOW)
        async with uow_factory() as uow:
            for item in (invoice, letter):
                await uow.document_types.add(item)
            await uow.fields.add(kind)
            await uow.documents.add(typed)
            await uow.documents.add(untyped)
            await uow.commit()
        async with uow_factory() as uow:
            in_use = uow.documents.field_in_use
            assert await in_use(kind.id)
            assert await in_use(kind.id, values=["a", "c"])
            assert not await in_use(kind.id, values=["c"])
            assert await in_use(kind.id, outside_types=[invoice.id])  # the untyped one
            assert await in_use(kind.id, outside_types=[letter.id])
            assert not await in_use(kind.id, values=["a"], outside_types=[invoice.id])
            assert await in_use(kind.id, values=["b"], outside_types=[invoice.id, letter.id])

    async def test_master_data_is_removed(self, uow_factory: UnitOfWorkFactory) -> None:
        tag, contact = Tag.create(name="old", now=NOW), Contact.create(name="Gone", now=NOW)
        async with uow_factory() as uow:
            await uow.tags.add(tag)
            await uow.contacts.add(contact)
            await uow.commit()
        async with uow_factory() as uow:
            await uow.tags.remove(tag.id)
            await uow.contacts.remove(contact.id)
            await uow.commit()
        async with uow_factory() as uow:
            assert await uow.tags.find(tag.id) is None
            assert await uow.contacts.find_by_name("gone") is None
            with pytest.raises(NotFoundError):
                await uow.tags.remove(tag.id)

    async def test_documents_exist_by_reference(self, uow_factory: UnitOfWorkFactory) -> None:
        owner, space = await owner_with_space(uow_factory)
        stranger, empty = await owner_with_space(uow_factory)
        contact = Contact.create(name="ACME", now=NOW)
        document_type = DocumentType.create(name="Invoice", now=NOW)
        tag = Tag.create(name="tax", now=NOW)
        field = FieldDefinition.create(name="note", data_type=FieldType.TEXT, now=NOW)
        document = builders.document(owner, space)
        document.apply_changes(
            DocumentChanges(
                contact_id=contact.id,
                document_type_id=document_type.id,
                tag_ids=frozenset({tag.id}),
                fields={field.id: "x"},
            ),
            {field.id: field},
            NOW,
        )
        async with uow_factory() as uow:
            await uow.contacts.add(contact)
            await uow.document_types.add(document_type)
            await uow.tags.add(tag)
            await uow.fields.add(field)
            await uow.documents.add(document)
            await uow.commit()
        unused_tag = Tag.create(name="unused", now=NOW)
        async with uow_factory() as uow:
            assert await uow.documents.exists(owner=owner.id)
            assert await uow.documents.exists(space=space.id)
            assert await uow.documents.exists(contact=contact.id)
            assert await uow.documents.exists(document_type=document_type.id)
            assert await uow.documents.exists(tag=tag.id)
            assert await uow.documents.exists(field=field.id)
            assert await uow.documents.exists(owner=owner.id, tag=tag.id)
            assert not await uow.documents.exists(owner=stranger.id)
            assert not await uow.documents.exists(space=empty.id)
            assert not await uow.documents.exists(owner=stranger.id, tag=tag.id)
            assert not await uow.documents.exists(tag=unused_tag.id)
            assert await uow.documents.exists(sha256=document.sha256)
            assert not await uow.documents.exists(sha256=builders.sha256("other"))
            with pytest.raises(ValueError):
                await uow.documents.exists()
