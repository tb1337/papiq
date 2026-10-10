import asyncio
from datetime import date, timedelta
from decimal import Decimal

import pytest

from papiq.core.domain.documents import Document, DocumentChanges
from papiq.core.domain.drawers import Drawer, ShareLevel
from papiq.core.domain.errors import ConcurrencyError, ConflictError, NotFoundError
from papiq.core.domain.fields import FieldDefinition, FieldType, Money, Url
from papiq.core.domain.ids import DocumentId, DrawerId, new_id
from papiq.core.domain.master_data import Contact, DocumentType, Tag
from papiq.core.domain.permissions import can_read_document
from papiq.core.domain.pipeline import Lane, Outcome, ProcessingStatus, Step, StepResult, StepRun
from papiq.core.domain.users import User
from papiq.core.ports import DocumentFilter, UnitOfWorkFactory
from tests import builders
from tests.builders import FAILED, NOW, OK, UNCERTAIN


async def seed(
    uow_factory: UnitOfWorkFactory, *users: User, drawers: tuple[Drawer, ...] = ()
) -> None:
    async with uow_factory() as uow:
        for user in users:
            await uow.users.add(user)
        for drawer in drawers:
            await uow.drawers.add(drawer)
        await uow.commit()


async def owner_with_drawer(uow_factory: UnitOfWorkFactory) -> tuple[User, Drawer]:
    owner = builders.user()
    drawer = builders.default_drawer(owner)
    await seed(uow_factory, owner, drawers=(drawer,))
    return owner, drawer


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
        owner, _ = await owner_with_drawer(uow_factory)
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
        owner, _ = await owner_with_drawer(uow_factory)
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
        owner, _ = await owner_with_drawer(uow_factory)
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
        owner, _ = await owner_with_drawer(uow_factory)
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

    # --- drawers --------------------------------------------------------------------------------

    async def test_drawer_round_trip(self, uow_factory: UnitOfWorkFactory) -> None:
        owner, reader, writer = builders.user(), builders.user(), builders.user()
        drawer = builders.drawer(owner, "Household")
        drawer.share(reader.id, ShareLevel.READ)
        drawer.share(writer.id, ShareLevel.READ_WRITE)
        await seed(uow_factory, owner, reader, writer, drawers=(drawer,))
        async with uow_factory() as uow:
            assert await uow.drawers.get(drawer.id) == drawer

    async def test_drawer_names_are_unique_per_owner(self, uow_factory: UnitOfWorkFactory) -> None:
        alice, bob = builders.user(), builders.user()
        await seed(
            uow_factory,
            alice,
            bob,
            drawers=(builders.drawer(alice, "Taxes"), builders.drawer(bob, "Taxes")),
        )
        with pytest.raises(ConflictError):
            await seed(uow_factory, drawers=(builders.drawer(alice, "TAXES"),))

    async def test_one_default_drawer_per_owner(self, uow_factory: UnitOfWorkFactory) -> None:
        owner, _ = await owner_with_drawer(uow_factory)
        second = Drawer(
            id=DrawerId(new_id()),
            owner_id=owner.id,
            name="Another default",
            is_default=True,
            created_at=NOW,
        )
        with pytest.raises(ConflictError):
            await seed(uow_factory, drawers=(second,))

    async def test_default_and_accessible_drawers(self, uow_factory: UnitOfWorkFactory) -> None:
        alice, bob, carol = builders.user(), builders.user(), builders.user()
        alice_default, bob_default = builders.default_drawer(alice), builders.default_drawer(bob)
        shared = builders.drawer(alice, "Shared")
        shared.share(bob.id, ShareLevel.READ)
        await seed(uow_factory, alice, bob, carol, drawers=(alice_default, bob_default, shared))
        async with uow_factory() as uow:
            assert await uow.drawers.get_default(alice.id) == alice_default
            with pytest.raises(NotFoundError):
                await uow.drawers.get_default(carol.id)
            accessible = {drawer.id for drawer in await uow.drawers.list_accessible(bob.id)}
            assert accessible == {bob_default.id, shared.id}
            assert await uow.drawers.list_accessible(carol.id) == []
            every = {drawer.id for drawer in await uow.drawers.list_all()}
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

    async def test_field_definition_round_trip(self, uow_factory: UnitOfWorkFactory) -> None:
        invoice = DocumentType.create(name="Invoice", now=NOW)
        bound = FieldDefinition.create(
            name="Billing period",
            data_type=FieldType.CHOICE,
            now=NOW,
            document_type_ids=[invoice.id],
            choices=["monthly", "yearly"],
        )
        global_ = FieldDefinition.create(name="Note", data_type=FieldType.TEXT, now=NOW)
        async with uow_factory() as uow:
            await uow.document_types.add(invoice)
            await uow.fields.add(bound)
            await uow.fields.add(global_)
            await uow.commit()
        async with uow_factory() as uow:
            assert await uow.fields.get(bound.id) == bound
            assert await uow.fields.get(global_.id) == global_
            assert await uow.fields.find_by_name("NOTE") == global_

    # --- documents ------------------------------------------------------------------------------

    async def test_document_round_trip_with_all_fields(
        self, uow_factory: UnitOfWorkFactory
    ) -> None:
        owner, drawer = await owner_with_drawer(uow_factory)
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
        }
        document = builders.document(owner, drawer)
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
        builders.run_pipeline(document, {Step.CLASSIFY: UNCERTAIN})
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
        owner, drawer = await owner_with_drawer(uow_factory)
        failed = builders.processed(owner, drawer, {Step.PARSE: FAILED})
        failed.retry(NOW)
        failed.pull_events()
        received = builders.document(owner, drawer)
        in_review = builders.processed(owner, drawer, {Step.CLASSIFY: UNCERTAIN})
        assert in_review.processing.status is ProcessingStatus.REVIEW
        async with uow_factory() as uow:
            for document in (failed, received, in_review):
                await uow.documents.add(document)
            await uow.commit()
        async with uow_factory() as uow:
            for document in (failed, received, in_review):
                assert await uow.documents.get(document.id) == document

    async def test_loaded_documents_carry_no_events(self, uow_factory: UnitOfWorkFactory) -> None:
        owner, drawer = await owner_with_drawer(uow_factory)
        document = builders.document(owner, drawer)
        builders.run_pipeline(document)  # events recorded but not pulled
        async with uow_factory() as uow:
            await uow.documents.add(document)
            await uow.commit()
        async with uow_factory() as uow:
            assert (await uow.documents.get(document.id)).pull_events() == []

    async def test_original_is_unique_per_owner(self, uow_factory: UnitOfWorkFactory) -> None:
        alice, alice_drawer = await owner_with_drawer(uow_factory)
        bob, bob_drawer = await owner_with_drawer(uow_factory)
        first = builders.document(alice, alice_drawer, content="same")
        async with uow_factory() as uow:
            await uow.documents.add(first)
            await uow.documents.add(builders.document(bob, bob_drawer, content="same"))
            await uow.commit()
        async with uow_factory() as uow:
            assert await uow.documents.find_by_sha256(alice.id, first.sha256) == first
            assert await uow.documents.find_by_sha256(alice.id, builders.sha256()) is None
        with pytest.raises(ConflictError):
            async with uow_factory() as uow:
                await uow.documents.add(builders.document(alice, alice_drawer, content="same"))
                await uow.commit()

    async def test_remove_document(self, uow_factory: UnitOfWorkFactory) -> None:
        owner, drawer = await owner_with_drawer(uow_factory)
        document = builders.document(owner, drawer)
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
        private = builders.default_drawer(owner)
        shared = builders.drawer(owner, "Shared")
        shared.share(reader.id, ShareLevel.READ)
        reader_default = builders.default_drawer(reader)
        await seed(uow_factory, owner, reader, stranger, drawers=(private, shared, reader_default))
        documents = [
            builders.processed(owner, private),
            builders.processed(owner, shared),
            builders.processed(owner, shared, {Step.CLASSIFY: UNCERTAIN}),
            builders.processed(owner, shared, {Step.OCR: FAILED}),
            builders.document(owner, shared),
            builders.processed(reader, shared),
            builders.processed(reader, shared, {Step.CLASSIFY: UNCERTAIN}),
            builders.processed(reader, reader_default),
        ]
        async with uow_factory() as uow:
            for document in documents:
                await uow.documents.add(document)
            await uow.commit()
        drawers = {drawer.id: drawer for drawer in (private, shared, reader_default)}
        async with uow_factory() as uow:
            for user in (owner, reader, stranger):
                expected = {
                    document.id
                    for document in documents
                    if can_read_document(user, document, drawers[document.drawer_id])
                }
                visible = {document.id for document in await uow.documents.list_visible_to(user.id)}
                assert visible == expected, user.username
                queried = await uow.documents.query_visible(user.id, DocumentFilter(), limit=100)
                assert {document.id for document in queried} == expected, user.username
                for lane in (Lane.YELLOW, Lane.RED, None):
                    only = DocumentFilter(lanes=frozenset({lane}), drawer=shared.id)
                    found = await uow.documents.query_visible(user.id, only, limit=100)
                    assert {d.id for d in found} <= expected, user.username
        assert len(expected) == 0  # the stranger sees nothing
        async with uow_factory() as uow:
            every = await uow.documents.query(DocumentFilter(), limit=100)
            assert {d.id for d in every} >= {d.id for d in documents}
            yellow = DocumentFilter(lanes=frozenset({Lane.YELLOW}), drawer=shared.id)
            yellow_ids = {d.id for d in await uow.documents.query(yellow, limit=100)}
            assert yellow_ids == {d.id for d in documents if d.lane is Lane.YELLOW}

    async def test_query_filters_and_pages(self, uow_factory: UnitOfWorkFactory) -> None:
        owner, drawer = await owner_with_drawer(uow_factory)
        other = builders.drawer(owner, "Other")
        await seed(uow_factory, drawers=(other,))
        contact = Contact.create(name="ACME", now=NOW)
        invoice = DocumentType.create(name="Invoice", now=NOW)
        tax, paid = Tag.create(name="tax", now=NOW), Tag.create(name="paid", now=NOW)
        documents = []
        for number in range(6):
            document = builders.processed(
                owner,
                other if number == 5 else drawer,
                {Step.CLASSIFY: UNCERTAIN} if number == 4 else None,
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
        processing = builders.document(owner, drawer)
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
            both_tags = DocumentFilter(tags=frozenset({tax.id, paid.id}))
            assert set(ids(await query(owner.id, both_tags, limit=9))) == {0, 1}
            assert set(ids(await query(owner.id, DocumentFilter(drawer=other.id), limit=9))) == {5}
            yellow = DocumentFilter(lanes=frozenset({Lane.YELLOW}))
            assert set(ids(await query(owner.id, yellow, limit=9))) == {4}
            in_processing = await query(owner.id, DocumentFilter(lanes=frozenset({None})), limit=9)
            assert [d.id for d in in_processing] == [processing.id]
            combined = DocumentFilter(
                contact=contact.id, document_type=invoice.id, tags=frozenset({paid.id})
            )
            assert set(ids(await query(owner.id, combined, limit=9))) == {0}

            # Pages of two, following the last id.
            pages: list[DocumentId] = []
            before = None
            while page := await query(owner.id, DocumentFilter(), before=before, limit=2):
                assert len(page) <= 2
                pages += [d.id for d in page]
                before = page[-1].id
            assert pages == [d.id for d in everything]

            # Over every document: the same filters and paging.
            assert [d.id for d in await query(owner.id, both_tags, limit=9)] == [
                d.id for d in await uow.documents.query(both_tags, limit=9)
            ]
            in_drawer = [d.id for d in everything if d.drawer_id == drawer.id]
            first = await uow.documents.query(DocumentFilter(drawer=drawer.id), limit=2)
            rest = await uow.documents.query(
                DocumentFilter(drawer=drawer.id), before=first[-1].id, limit=100
            )
            assert [d.id for d in [*first, *rest]] == in_drawer

    async def test_removing_a_document_removes_its_processing_log(
        self, uow_factory: UnitOfWorkFactory
    ) -> None:
        owner, drawer = await owner_with_drawer(uow_factory)
        document = builders.document(owner, drawer)
        entry = StepRun(
            document_id=document.id,
            step=Step.RECEIVE,
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
        owner, drawer = await owner_with_drawer(uow_factory)
        document, other = builders.document(owner, drawer), builders.document(owner, drawer)
        entries = [
            StepRun(
                document_id=document.id,
                step=step,
                run=1,
                result=result,
                pipeline_version="0.1.0",
                started_at=NOW,
                duration=timedelta(milliseconds=250),
            )
            for step, result in [
                (Step.RECEIVE, OK),
                (
                    Step.OCR,
                    StepResult(
                        outcome=Outcome.UNCERTAIN,
                        reason="low quality scan",
                        confidence=0.25,
                        model_version="tesseract 5.5",
                        input={"pages": 2},
                        output={"languages": ["deu", "eng"], "ratio": 0.5, "ok": None},
                    ),
                ),
                (Step.PARSE, FAILED),
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

    async def test_users_and_drawers_are_removed(self, uow_factory: UnitOfWorkFactory) -> None:
        owner, drawer = await owner_with_drawer(uow_factory)
        other = builders.user()
        shared = builders.drawer(owner)
        shared.share(other.id, ShareLevel.READ)
        await seed(uow_factory, other, drawers=(shared,))
        async with uow_factory() as uow:
            await uow.drawers.remove(shared.id)
            await uow.drawers.remove(drawer.id)
            await uow.users.remove(owner.id)
            await uow.commit()
        async with uow_factory() as uow:
            assert await uow.users.find(owner.id) is None
            assert await uow.drawers.find(shared.id) is None
            assert await uow.drawers.list_accessible(other.id) == []
            with pytest.raises(NotFoundError):
                await uow.users.remove(owner.id)
            with pytest.raises(NotFoundError):
                await uow.drawers.remove(drawer.id)

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
        owner, drawer = await owner_with_drawer(uow_factory)
        invoice = DocumentType.create(name="Invoice", now=NOW)
        letter = DocumentType.create(name="Letter", now=NOW)
        kind = FieldDefinition.create(
            name="Kind", data_type=FieldType.CHOICE, now=NOW, choices=["a", "b", "c"]
        )
        typed, untyped = builders.document(owner, drawer), builders.document(owner, drawer)
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
        owner, drawer = await owner_with_drawer(uow_factory)
        stranger, empty = await owner_with_drawer(uow_factory)
        contact = Contact.create(name="ACME", now=NOW)
        document_type = DocumentType.create(name="Invoice", now=NOW)
        tag = Tag.create(name="tax", now=NOW)
        field = FieldDefinition.create(name="note", data_type=FieldType.TEXT, now=NOW)
        document = builders.document(owner, drawer)
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
            assert await uow.documents.exists(drawer=drawer.id)
            assert await uow.documents.exists(contact=contact.id)
            assert await uow.documents.exists(document_type=document_type.id)
            assert await uow.documents.exists(tag=tag.id)
            assert await uow.documents.exists(field=field.id)
            assert await uow.documents.exists(owner=owner.id, tag=tag.id)
            assert not await uow.documents.exists(owner=stranger.id)
            assert not await uow.documents.exists(drawer=empty.id)
            assert not await uow.documents.exists(owner=stranger.id, tag=tag.id)
            assert not await uow.documents.exists(tag=unused_tag.id)
            assert await uow.documents.exists(sha256=document.sha256)
            assert not await uow.documents.exists(sha256=builders.sha256("other"))
            with pytest.raises(ValueError):
                await uow.documents.exists()
