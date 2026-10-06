import asyncio
from datetime import date, timedelta
from decimal import Decimal

import pytest

from papiq.core.domain.attributes import AttributeDefinition, AttributeType, Money, Url
from papiq.core.domain.documents import DocumentChanges
from papiq.core.domain.drawers import Drawer, ShareLevel
from papiq.core.domain.errors import ConcurrencyError, ConflictError, NotFoundError
from papiq.core.domain.ids import DrawerId, new_id
from papiq.core.domain.master_data import Contact, DocumentType, Tag
from papiq.core.domain.permissions import can_read_document
from papiq.core.domain.pipeline import Outcome, Step, StepResult, StepRun
from papiq.core.domain.users import User
from papiq.core.ports import UnitOfWorkFactory
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

        async def rename(name: str) -> str:
            async with uow_factory() as uow:
                user = await uow.users.get(owner.id)
                await asyncio.sleep(0)
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

    async def test_attribute_definition_round_trip(self, uow_factory: UnitOfWorkFactory) -> None:
        invoice = DocumentType.create(name="Invoice", now=NOW)
        bound = AttributeDefinition.create(
            name="Billing period",
            data_type=AttributeType.CHOICE,
            now=NOW,
            document_type_ids=[invoice.id],
            choices=["monthly", "yearly"],
        )
        global_ = AttributeDefinition.create(name="Note", data_type=AttributeType.TEXT, now=NOW)
        async with uow_factory() as uow:
            await uow.document_types.add(invoice)
            await uow.attributes.add(bound)
            await uow.attributes.add(global_)
            await uow.commit()
        async with uow_factory() as uow:
            assert await uow.attributes.get(bound.id) == bound
            assert await uow.attributes.get(global_.id) == global_
            assert await uow.attributes.find_by_name("NOTE") == global_

    # --- documents ------------------------------------------------------------------------------

    async def test_document_round_trip_with_all_fields(
        self, uow_factory: UnitOfWorkFactory
    ) -> None:
        owner, drawer = await owner_with_drawer(uow_factory)
        contact = Contact.create(name="Bank", now=NOW)
        invoice = DocumentType.create(name="Invoice", now=NOW)
        tags = [Tag.create(name=name, now=NOW) for name in ("a", "b")]
        definitions = [
            AttributeDefinition.create(
                name=f"field {data_type}",
                data_type=data_type,
                now=NOW,
                choices=["x", "y"] if data_type is AttributeType.CHOICE else (),
            )
            for data_type in AttributeType
        ]
        values: dict[AttributeType, object] = {
            AttributeType.TEXT: "Contract 7",
            AttributeType.NUMBER: Decimal("1234.5678"),
            AttributeType.AMOUNT: Money(Decimal("-12.30"), "CHF"),
            AttributeType.DATE: date(2026, 2, 28),
            AttributeType.BOOLEAN: False,
            AttributeType.CHOICE: "y",
            AttributeType.LINK: Url("https://example.org/contract?id=7"),
        }
        document = builders.document(owner, drawer)
        document.apply_changes(
            DocumentChanges(
                title="Statement",
                contact_id=contact.id,
                document_type_id=invoice.id,
                tag_ids=frozenset(tag.id for tag in tags),
                document_date=date(2026, 3, 1),
                attributes={d.id: values[d.data_type] for d in definitions},
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
                await uow.attributes.add(definition)
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
        async with uow_factory() as uow:
            await uow.documents.add(failed)
            await uow.documents.add(received)
            await uow.commit()
        async with uow_factory() as uow:
            assert await uow.documents.get(failed.id) == failed
            assert await uow.documents.get(received.id) == received

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
        assert len(expected) == 0  # the stranger sees nothing

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
