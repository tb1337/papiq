"""Tests of the SQL adapter beyond the port contracts, run on SQLite and on Postgres.

Test modules subclass the suites and provide the fixtures `database` (migrated, empty),
`uow_factory`, `event_bus_factory` and, for the migration suite, `empty_database`.
"""

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Connection, insert, inspect, select

from papiq.adapters.outbound.memory import ManualClock, MemoryObjectStore
from papiq.adapters.outbound.sql import Database, SqlUnitOfWorkFactory, migrate, schema_state
from papiq.adapters.outbound.sql import tables as t
from papiq.adapters.outbound.sql.migrations import downgrade
from papiq.adapters.outbound.sql.types import UtcDateTime, json_type
from papiq.core.domain.documents import Document, DocumentChanges
from papiq.core.domain.errors import ConflictError, DuplicateDocumentError
from papiq.core.domain.events import DomainEvent
from papiq.core.domain.fields import FieldDefinition, FieldType, Money
from papiq.core.domain.ids import new_id
from papiq.core.domain.jobs import Job
from papiq.core.domain.master_data import TagScope
from papiq.core.domain.pipeline import NODES, Lane, Node
from papiq.core.domain.users import User
from papiq.core.ports import DeliveryRetry, EventBus, UnitOfWorkFactory
from papiq.core.services import master_data
from papiq.core.services.documents import DocumentService
from papiq.core.services.master_data import MasterDataService
from papiq.core.services.pipeline import PipelineService, PlaceholderNode
from tests import builders
from tests.builders import NOW, incoming
from tests.contracts.event_bus import EventBusFactory, Recorder, publish, received
from tests.contracts.unit_of_work import owner_with_space

LEASE = timedelta(minutes=5)


class SqlAdapterSuite:
    # --- types ----------------------------------------------------------------------------------

    async def test_decimals_keep_their_exact_value_and_scale(
        self, uow_factory: UnitOfWorkFactory
    ) -> None:
        owner, space = await owner_with_space(uow_factory)
        number = FieldDefinition.create(name="n", data_type=FieldType.NUMBER, now=NOW)
        amount = FieldDefinition.create(name="a", data_type=FieldType.AMOUNT, now=NOW)
        precise = Decimal("12345678901234567890.123456789012345678")
        money = Money(Decimal("0.10"), "EUR")
        document = builders.document(owner, space)
        document.apply_changes(
            DocumentChanges(fields={number.id: precise, amount.id: money}),
            {number.id: number, amount.id: amount},
            NOW,
        )
        async with uow_factory() as uow:
            await uow.fields.add(number)
            await uow.fields.add(amount)
            await uow.documents.add(document)
            await uow.commit()
        async with uow_factory() as uow:
            stored = (await uow.documents.get(document.id)).fields
        assert str(stored[number.id]) == str(precise)
        stored_money = stored[amount.id]
        assert isinstance(stored_money, Money)
        assert str(stored_money.amount) == "0.10"  # scale kept, no binary float

    async def test_timestamps_are_utc_with_microseconds(
        self, uow_factory: UnitOfWorkFactory
    ) -> None:
        moment = datetime(2026, 3, 29, 1, 30, 15, 123456, tzinfo=UTC)
        async with uow_factory() as uow:
            id = await uow.jobs.enqueue("test", {}, run_at=moment)
            await uow.commit()
        assert id is not None
        async with uow_factory() as uow:
            job = await uow.jobs.get(id)
        assert job.run_at == moment
        assert job.run_at.tzinfo is UTC

    async def test_due_jobs_compare_as_instants(self, uow_factory: UnitOfWorkFactory) -> None:
        """Comparisons in SQL follow time, also across days, months and microseconds."""
        times = [
            datetime(2026, 12, 31, 23, 59, 59, 999999, tzinfo=UTC),
            datetime(2027, 1, 1, tzinfo=UTC),
            datetime(2027, 1, 1, 0, 0, 0, 1, tzinfo=UTC),
        ]
        async with uow_factory() as uow:
            for moment in reversed(times):
                await uow.jobs.enqueue("test", {}, run_at=moment)
            await uow.commit()
        claimed: list[datetime] = []
        for now in times:
            async with uow_factory() as uow:
                job = await uow.jobs.claim(now=now, lease=LEASE)
                assert job is not None
                claimed.append(job.run_at)
                assert await uow.jobs.claim(now=now, lease=LEASE) is None
                await uow.commit()
        assert claimed == times

    # --- concurrency ----------------------------------------------------------------------------

    async def test_many_workers_claim_every_job_exactly_once(
        self, uow_factory: UnitOfWorkFactory
    ) -> None:
        async with uow_factory() as uow:
            enqueued = {await uow.jobs.enqueue("test", {"n": n}, run_at=NOW) for n in range(30)}
            await uow.commit()

        async def worker() -> list[Job]:
            claimed: list[Job] = []
            while True:
                async with uow_factory() as uow:
                    job = await uow.jobs.claim(now=NOW, lease=LEASE)
                    await uow.commit()
                if job is None:
                    return claimed
                claimed.append(job)
                async with uow_factory() as uow:
                    await uow.jobs.complete(job)
                    await uow.commit()

        results = await asyncio.gather(*(worker() for _ in range(6)))
        claimed = [job.id for jobs in results for job in jobs]
        assert None not in enqueued
        assert sorted(claimed) == sorted(id for id in enqueued if id)  # each job exactly once

    async def test_concurrent_duplicate_originals_conflict_in_the_database(
        self, uow_factory: UnitOfWorkFactory
    ) -> None:
        """Both units find no duplicate, then both add the same original: the unique index
        rejects one of them."""
        owner, space = await owner_with_space(uow_factory)
        both_checked = asyncio.Barrier(2)

        async def add() -> Document:
            document = builders.document(owner, space, content="same")
            async with uow_factory() as uow:
                assert await uow.documents.find_by_sha256(owner.id, document.sha256) is None
                async with asyncio.timeout(10):
                    await both_checked.wait()
                await uow.documents.add(document)
                await uow.commit()
            return document

        results = await asyncio.gather(add(), add(), return_exceptions=True)
        assert len([r for r in results if isinstance(r, Document)]) == 1
        assert len([r for r in results if isinstance(r, ConflictError)]) == 1

    async def test_concurrent_uploads_of_the_same_file_create_one_document(
        self, uow_factory: UnitOfWorkFactory
    ) -> None:
        owner, _ = await owner_with_space(uow_factory)
        pipeline = PipelineService(
            uow_factory,
            ManualClock(NOW),
            MemoryObjectStore(),
            {node: PlaceholderNode() for node in NODES if node is not Node.RECEIVE},
            pipeline_version="test",
        )

        async def upload() -> Document:
            return await pipeline.receive(owner.id, incoming(b"%PDF-1.7 same"), filename="a.pdf")

        results = await asyncio.gather(*(upload() for _ in range(3)), return_exceptions=True)
        assert len([r for r in results if isinstance(r, Document)]) == 1, results
        assert all(isinstance(r, Document | DuplicateDocumentError) for r in results), results
        async with uow_factory() as uow:
            assert len(await uow.documents.list_visible_to(owner.id)) == 1

    async def test_concurrent_tag_moves_make_no_cycle(self, uow_factory: UnitOfWorkFactory) -> None:
        """Moving A under B and B under A at once: one move waits for the other and sees it."""
        admin = builders.admin()
        await _seed_user(uow_factory, admin)
        service = MasterDataService(uow_factory, ManualClock(NOW), builders.TEMPLATES)
        a = await service.create_tag(admin.id, "A")
        b = await service.create_tag(admin.id, "B")
        results = await asyncio.gather(
            service.change_tag(admin.id, a.id, parent_id=b.id),
            service.change_tag(admin.id, b.id, parent_id=a.id),
            return_exceptions=True,
        )
        assert sorted(type(result).__name__ for result in results) == ["Tag", "ValidationError"]
        async with uow_factory() as uow:
            tags = {tag.id: tag for tag in await uow.tags.list_all()}
        assert None in {tags[a.id].parent_id, tags[b.id].parent_id}

    async def test_a_moved_tag_leaves_documents_and_groups_the_tag_below(
        self, uow_factory: UnitOfWorkFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The job after a move, a page of documents at a time: what carries a tag and one now
        above it keeps the one below; the lane, the confirmation and other documents stay."""
        monkeypatch.setattr(master_data, "_NORMALISE_BATCH", 2)
        owner, space = await owner_with_space(uow_factory)
        admin = builders.admin()
        await _seed_user(uow_factory, admin)
        service = MasterDataService(uow_factory, ManualClock(NOW), builders.TEMPLATES)
        insurance = await service.create_tag(admin.id, "Insurance")
        health = await service.create_tag(admin.id, "Health")
        dental = await service.create_tag(admin.id, "Dental", parent_id=health.id)
        insurer = await service.create_tag(admin.id, "Insurer", scope=TagScope.CONTACT)
        kassen = await service.create_tag(admin.id, "Kassen", scope=TagScope.CONTACT)
        bills = await service.create_tag(admin.id, "Bills", scope=TagScope.DOCUMENT_TYPE)
        medical = await service.create_tag(admin.id, "Medical", scope=TagScope.DOCUMENT_TYPE)
        nord = await service.create_contact(admin.id, "Nord", group_ids=[insurer.id, kassen.id])
        invoice = await service.create_document_type(
            admin.id, "Invoice", group_ids=[bills.id, medical.id]
        )
        carried = [
            {insurance.id, health.id},
            {insurance.id, dental.id},
            {insurance.id},
            {health.id},
            {insurance.id, health.id},
        ]
        documents = []
        async with uow_factory() as uow:
            for number, tags in enumerate(carried):
                document = builders.run_pipeline(
                    builders.document(owner, space, content=str(number))
                )
                document.tag_ids = set(tags)
                if number == 0:
                    document.mark_confirmed(NOW)
                await uow.documents.add(document)
                documents.append(document)
            await uow.commit()
        await service.change_tag(admin.id, health.id, parent_id=insurance.id)
        await service.change_tag(admin.id, kassen.id, parent_id=insurer.id)
        await service.change_tag(admin.id, medical.id, parent_id=bills.id)
        # Health: four documents carry it or Dental, two per step, and a last step finds none.
        assert [await service.run_next_job() for _ in range(6)] == [True] * 5 + [False]
        async with uow_factory() as uow:
            stored = [await uow.documents.get(document.id) for document in documents]
            assert (await uow.contacts.get(nord.id)).group_ids == {kassen.id}
            assert (await uow.document_types.get(invoice.id)).group_ids == {medical.id}
        assert [document.tag_ids for document in stored] == [
            {health.id},
            {dental.id},
            {insurance.id},
            {health.id},
            {health.id},
        ]
        assert [document.version for document in stored] == [2, 2, 1, 1, 2]
        assert {document.lane for document in stored} == {Lane.GREEN}
        assert [document.confirmed_at for document in stored[:2]] == [NOW, None]

    async def test_groups_and_topics_count_their_contacts_and_types(
        self, uow_factory: UnitOfWorkFactory
    ) -> None:
        """Each contact (type) once at its group or topic and at those above it, from the
        stored groups and topics of contacts and types."""
        admin = builders.admin()
        await _seed_user(uow_factory, admin)
        service = MasterDataService(uow_factory, ManualClock(NOW), builders.TEMPLATES)
        insurer = await service.create_tag(admin.id, "Insurer", scope=TagScope.CONTACT)
        health = await service.create_tag(
            admin.id, "Health insurer", scope=TagScope.CONTACT, parent_id=insurer.id
        )
        work = await service.create_tag(admin.id, "Work", scope=TagScope.TOPIC)
        bills = await service.create_tag(admin.id, "Bills", scope=TagScope.DOCUMENT_TYPE)
        medical = await service.create_tag(
            admin.id, "Medical", scope=TagScope.DOCUMENT_TYPE, parent_id=bills.id
        )
        await service.create_contact(
            admin.id, "Nord", group_ids=[insurer.id, health.id], topic_ids=[work.id]
        )
        await service.create_contact(admin.id, "Süd", group_ids=[health.id], topic_ids=[work.id])
        await service.create_document_type(admin.id, "Invoice", group_ids=[medical.id])
        members = await service.tag_members(admin.id)
        assert members.contacts == {insurer.id: 2, health.id: 2, work.id: 2}
        assert members.document_types == {bills.id: 1, medical.id: 1}

    async def test_a_topic_is_not_given_while_its_contact_gives_it_up(
        self, uow_factory: UnitOfWorkFactory
    ) -> None:
        """Taking a topic away from a contact and giving it to one of its documents at once:
        one waits for the other and sees it, so no document keeps a topic its contact does
        not enable."""
        owner, space = await owner_with_space(uow_factory)
        admin = builders.admin()
        await _seed_user(uow_factory, admin)
        master = MasterDataService(uow_factory, ManualClock(NOW), builders.TEMPLATES)
        documents = DocumentService(uow_factory, ManualClock(NOW), MemoryObjectStore())
        work = await master.create_tag(admin.id, "Work", scope=TagScope.TOPIC)
        nord = await master.create_contact(admin.id, "Nord", topic_ids=[work.id])
        document = builders.document(owner, space)
        async with uow_factory() as uow:
            await uow.documents.add(document)
            await uow.commit()
        await documents.update_metadata(owner.id, document.id, DocumentChanges(contact_id=nord.id))
        results = await asyncio.gather(
            master.change_contact(admin.id, nord.id, topic_ids=[]),
            documents.update_metadata(owner.id, document.id, DocumentChanges(topic_id=work.id)),
            return_exceptions=True,
        )
        assert sorted(type(result).__name__ for result in results) in (
            ["Contact", "ValidationError"],
            ["ConflictError", "Document"],
        ), results
        async with uow_factory() as uow:
            stored = await uow.documents.get(document.id)
            contact = await uow.contacts.get(nord.id)
        assert stored.topic_id is None or stored.topic_id in contact.topic_ids

    async def test_a_failed_write_leaves_only_rollback(
        self, uow_factory: UnitOfWorkFactory
    ) -> None:
        user = builders.user()
        await _seed_user(uow_factory, user)
        async with uow_factory() as uow:
            with pytest.raises(ConflictError):
                await uow.users.add(user)
            with pytest.raises(RuntimeError):
                await uow.users.find(user.id)
            with pytest.raises(RuntimeError):
                await uow.commit()

    # --- events ---------------------------------------------------------------------------------

    async def test_handlers_may_write_while_dispatching(
        self, uow_factory: UnitOfWorkFactory, event_bus_factory: Callable[[], EventBus]
    ) -> None:
        """Handlers run outside of the dispatcher's transactions: on SQLite, a handler that
        writes must not wait for the write lock of its own dispatcher."""
        bus = event_bus_factory()

        async def index(event: DomainEvent) -> None:
            async with uow_factory() as uow:
                await uow.jobs.enqueue("index", {"event": str(event.id)}, run_at=NOW)
                await uow.commit()

        bus.subscribe("index", index)
        await publish(uow_factory, received(1), received(2))
        assert await bus.dispatch() == 2
        async with uow_factory() as uow:
            assert await uow.jobs.claim(now=NOW, lease=LEASE, kinds=["index"]) is not None

    async def test_events_of_unknown_type_are_skipped(
        self,
        uow_factory: UnitOfWorkFactory,
        event_bus_factory: Callable[[], EventBus],
        database: Database,
    ) -> None:
        """An event type written by a newer version is skipped, not retried forever."""
        bus = event_bus_factory()
        recorder = Recorder()
        bus.subscribe("test", recorder)
        await bus.dispatch()  # creates the subscription
        async with database.writing() as connection:
            await connection.execute(
                insert(t.outbox).values(
                    event_id=new_id(),
                    type="document.from_the_future",
                    occurred_at=NOW,
                    payload={},
                    recorded_at=datetime.now(UTC),
                )
            )
        known = received(1)
        await publish(uow_factory, known)
        assert await bus.dispatch() == 1
        assert await bus.dispatch() == 0
        assert recorder.received == [known]
        async with database.reading() as connection:
            assert (await connection.execute(select(t.event_retries))).all() == []

    async def test_failed_deliveries_are_kept_per_subscriber(
        self,
        uow_factory: UnitOfWorkFactory,
        event_bus_factory: EventBusFactory,
        database: Database,
    ) -> None:
        clock = ManualClock(NOW)
        bus = event_bus_factory(clock=clock, retry=DeliveryRetry(delay=timedelta(seconds=10)))
        failing = Recorder(failures=3)
        bus.subscribe("failing", failing)
        await publish(uow_factory, received(1))
        await bus.dispatch()
        clock.advance(timedelta(seconds=10))
        await bus.dispatch()
        async with database.reading() as connection:
            rows = (await connection.execute(select(t.event_retries))).all()
        assert [(row.subscriber, row.attempts) for row in rows] == [("failing", 2)]
        assert "handler failed" in rows[0].last_error
        assert rows[0].retry_at == NOW + timedelta(seconds=30)

    async def test_enqueue_with_dedup_key_many_times(self, uow_factory: UnitOfWorkFactory) -> None:
        """Postgres plans a prepared statement generically from its sixth run on; the partial
        index of the dedup key must still match then."""
        for number in range(12):
            async with uow_factory() as uow:
                assert await uow.jobs.enqueue("test", {}, run_at=NOW, dedup_key=f"k{number}")
                assert (
                    await uow.jobs.enqueue("test", {}, run_at=NOW, dedup_key=f"k{number}") is None
                )
                await uow.commit()

    async def test_dispatch_logs_at_info_level(
        self,
        uow_factory: UnitOfWorkFactory,
        event_bus_factory: EventBusFactory,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Log records must not use reserved field names (`name` raised KeyError)."""
        caplog.set_level(logging.INFO)
        bus = event_bus_factory()
        recorder = Recorder()
        bus.subscribe("index", recorder)
        await publish(uow_factory, received(1))
        assert await bus.dispatch() == 1
        assert any(record.message == "event subscription created" for record in caplog.records)


class MigrationSuite:
    """Needs the fixtures `empty_database` and `model_database`: two databases without
    schema."""

    async def test_upgrade_creates_the_schema_on_an_empty_database(
        self, empty_database: Database
    ) -> None:
        await migrate(empty_database)
        await migrate(empty_database)  # already up to date: nothing to do
        async with empty_database.reading() as connection:
            names = await connection.run_sync(lambda sync: inspect(sync).get_table_names())
        assert set(names) == {*t.metadata.tables, "alembic_version"}

    async def test_schema_state_tells_outdated_current_and_newer(
        self, empty_database: Database
    ) -> None:
        assert await schema_state(empty_database) == "outdated"  # nothing there yet
        await migrate(empty_database, "0001")
        assert await schema_state(empty_database) == "outdated"  # older
        await migrate(empty_database)
        assert await schema_state(empty_database) == "current"
        async with empty_database.writing() as connection:
            await connection.exec_driver_sql("UPDATE alembic_version SET version_num = '0000'")
        assert await schema_state(empty_database) == "newer"  # not a revision of this version

    async def test_attributes_become_fields_and_back(self, empty_database: Database) -> None:
        """Revision 0007 moves definitions, their types and values and renames stored names by
        their place; text a person wrote stays. The downgrade restores them."""
        await migrate(empty_database, "0006")
        ids = {name: new_id() for name in ("user", "space", "document", "field", "type", "rule")}
        field, document = str(ids["field"]), str(ids["document"])
        check = {"field": f"attribute:{field}", "outcome": "ok", "reason": "attribute"}
        condition = {"field": "attribute", "op": "is", "value": "x", "attribute_id": field}
        text = {"field": "text", "op": "contains", "value": "attribute"}
        rows: list[tuple[sa.TableClause | sa.Table, dict[str, Any]]] = [
            (t.users, {"id": ids["user"], "username": "a", "username_key": "a", "role": "admin"}),
            (_OLD["drawers"], {"id": ids["space"], "owner_id": ids["user"], "name_key": "d"}),
            (t.document_types, {"id": ids["type"], "name": "Invoice", "name_key": "invoice"}),
            (_OLD["definitions"], {"id": ids["field"], "name": "Due", "name_key": "due"}),
            (_OLD["types"], {"attribute_id": ids["field"], "document_type_id": ids["type"]}),
            (
                _drawer(_before_0009(t.documents)),
                {
                    "id": ids["document"],
                    "owner_id": ids["user"],
                    "drawer_id": ids["space"],
                    "processing_step": "apply_rules",
                    "processing_outcomes": {**_OK_UP_TO_CLASSIFY, "extract_attributes": "ok"},
                },
            ),
            (_OLD["values"], {"document_id": ids["document"], "attribute_id": ids["field"]}),
            (
                _before_0009(t.processing_log),
                {
                    "document_id": ids["document"],
                    "step": "extract_attributes",
                    "input": {"prompt": "extract-1"},
                    "output": {"answer": {"attributes": {"a1": "attributes"}}, "fields": [check]},
                },
            ),
            (t.rules, {"id": ids["rule"], "scope": "global", "current_version": 1}),
            (
                t.rule_versions,
                {
                    "rule_id": ids["rule"],
                    "number": 1,
                    "conditions": {"all": [condition, text], "negate": False},
                    "actions": [
                        {"type": "set_attribute", "attribute_id": field, "value": "x"},
                        {"type": "set_title", "title": "set_attribute"},
                    ],
                },
            ),
            (
                t.jobs,
                {
                    "id": new_id(),
                    "kind": "pipeline.step",
                    "payload": {"document_id": document, "step": "extract_attributes", "run": 1},
                    "dedup_key": f"{document}:1:extract_attributes",
                },
            ),
            (
                t.outbox,
                {
                    "event_id": new_id(),
                    "type": "document.updated",
                    "payload": {"document_id": document, "fields": ["title", "attributes"]},
                },
            ),
        ]
        async with empty_database.writing() as connection:
            for table, values in rows:
                columns = table.c
                defaults = {k: v for k, v in _DEFAULTS.items() if k in columns}
                await connection.execute(insert(table).values({**defaults, **values}))
        before = await _stored(empty_database, old=True)

        await migrate(empty_database, "0015")  # 0016 renames the nodes it reads
        after = await _stored(empty_database, old=False)
        assert after["definition"] == (ids["field"], "Due", "date")
        assert after["scope"] == (ids["field"], ids["type"])
        assert after["value"] == (ids["document"], ids["field"], "text", "Monday")
        assert after["document"] == ("apply_rules", {**_OK_UP_TO_CLASSIFY, "extract_fields": "ok"})
        assert after["log"] == (
            "extract_fields",
            {
                "answer": {"fields": {"a1": "attributes"}},
                "fields": [{**check, "field": f"field:{field}"}],
            },
        )
        assert after["rule"] == (
            {
                "all": [{"field": "field", "op": "is", "value": "x", "field_id": field}, text],
                "negate": False,
            },
            [
                {"type": "set_field", "field_id": field, "value": "x"},
                {"type": "set_title", "title": "set_attribute"},
            ],
        )
        assert after["job"] == (
            {"document_id": document, "node": "extract_fields", "run": 1},
            f"{document}:1:extract_fields",
        )
        assert after["event"] == {"document_id": document, "fields": ["title", "fields"]}

        await downgrade(empty_database, "0006")
        restored = await _stored(empty_database, old=True)
        # The answer's key stays `fields`: checks were stored under `fields` before as well.
        before["log"][1]["answer"] = {"fields": {"a1": "attributes"}}
        assert restored == before

    async def test_steps_become_nodes_and_back(self, empty_database: Database) -> None:
        """Revision 0009 renames the columns, the job kind, the payload keys and the event type
        `document.step_completed` wherever it is stored; the downgrade restores them."""
        await migrate(empty_database, "0008")
        ids = {name: new_id() for name in ("user", "space", "document", "webhook")}
        document = str(ids["document"])
        rows: list[tuple[sa.TableClause | sa.Table, dict[str, Any]]] = [
            (t.users, {"id": ids["user"], "username": "a", "username_key": "a", "role": "admin"}),
            (_OLD["drawers"], {"id": ids["space"], "owner_id": ids["user"], "name_key": "d"}),
            (
                _drawer(_before_0009(t.documents)),
                {
                    "id": ids["document"],
                    "owner_id": ids["user"],
                    "drawer_id": ids["space"],
                    "processing_step": "ocr",
                    "processing_outcomes": {"receive": "ok"},
                },
            ),
            (
                _before_0009(t.processing_log),
                {"document_id": ids["document"], "step": "receive", "input": {}, "output": {}},
            ),
            (
                t.jobs,
                {
                    "id": new_id(),
                    "kind": "pipeline.step",
                    "payload": {"document_id": document, "step": "ocr", "run": 1},
                    "dedup_key": f"{document}:1:ocr",
                },
            ),
            (
                t.outbox,
                {
                    "event_id": new_id(),
                    "type": "document.step_completed",
                    "payload": {"document_id": document, "step": "receive", "run": 1},
                },
            ),
            (
                t.webhooks,
                {
                    "id": ids["webhook"],
                    "owner_id": ids["user"],
                    "url": "https://example.org/hook",
                    "event_types": ["document.step_completed", "document.filed"],
                    "secret": b"s",
                    "active": True,
                    "failed_streak": 0,
                },
            ),
            (
                t.webhook_deliveries,
                {
                    "id": new_id(),
                    "webhook_id": ids["webhook"],
                    "event_id": new_id(),
                    "event_type": "document.step_completed",
                    "attempt": 1,
                    "duration_ms": 1,
                },
            ),
        ]
        async with empty_database.writing() as connection:
            for table, values in rows:
                defaults = {k: v for k, v in _DEFAULTS.items() if k in table.c}
                await connection.execute(insert(table).values({**defaults, **values}))
        before = await _node_names(empty_database, old=True)

        await migrate(empty_database)
        after = await _node_names(empty_database, old=False)
        assert after == {
            "document": "ocr",
            "log": "receive",
            "job": ("pipeline.node", {"document_id": document, "node": "ocr", "run": 1}),
            "event": (
                "document.node_completed",
                {"document_id": document, "node": "receive", "run": 1},
            ),
            "webhook": ["document.node_completed", "document.filed"],
            "delivery": "document.node_completed",
        }

        await downgrade(empty_database, "0008")
        assert await _node_names(empty_database, old=True) == before
        assert before["job"][0] == "pipeline.step"

    async def test_the_current_node_and_the_outcomes_become_node_states_and_back(
        self, empty_database: Database
    ) -> None:
        """Revision 0010 turns the current node and the outcomes into the state of every node,
        with the times of the latest log entry; `pages` is skipped. The downgrade derives the
        current node and the outcomes again."""
        await migrate(empty_database, "0009")
        ids = {name: new_id() for name in ("user", "space", "a", "b", "c", "d")}
        second, third = NOW + timedelta(seconds=1), NOW + timedelta(seconds=3)
        rules_ok = {**_OK_UP_TO_CLASSIFY, "extract_fields": "ok", "apply_rules": "ok"}
        documents = {
            "a": ("processing", "parse", {"receive": "ok", "ocr": "ok"}),
            "b": ("failed", "ocr", {"receive": "ok", "ocr": "failed"}),
            "c": ("review", "file", {**rules_ok, "classify": "uncertain"}),
            "d": ("completed", None, {**rules_ok, "file": "ok"}),
        }
        rows: list[tuple[sa.TableClause | sa.Table, dict[str, Any]]] = [
            (t.users, {"id": ids["user"], "username": "a", "username_key": "a", "role": "admin"}),
            (_OLD["drawers"], {"id": ids["space"], "owner_id": ids["user"], "name_key": "d"}),
        ]
        for name, (status, current, outcomes) in documents.items():
            rows.append(
                (
                    _drawer(_before_0010(t.documents)),
                    {
                        "id": ids[name],
                        "owner_id": ids["user"],
                        "drawer_id": ids["space"],
                        "sha256": name * 64,
                        "processing_status": status,
                        "processing_node": current,
                        "processing_outcomes": outcomes,
                        "updated_at": third,
                    },
                )
            )
        log = {
            "document_id": ids["a"],
            "input": {},
            "output": {},
            "started_at": NOW,
            "duration_us": 1_000_000,
        }
        rows += [
            (t.processing_log, {**log, "node": "receive"}),
            (t.processing_log, {**log, "node": "ocr", "run": 1, "started_at": NOW}),
            (t.processing_log, {**log, "node": "ocr", "run": 2, "started_at": second}),
            (
                t.processing_log,
                {**log, "node": "ocr", "run": 3, "model_version": "person", "started_at": third},
            ),
            (t.processing_log, {**log, "document_id": ids["d"], "node": "apply_rules"}),
            (
                t.processing_log,
                {
                    **log,
                    "document_id": ids["d"],
                    "node": "apply_rules",
                    "model_version": "rules:apply",
                    "started_at": third,
                },
            ),
        ]
        async with empty_database.writing() as connection:
            for table, values in rows:
                defaults = {k: v for k, v in _DEFAULTS.items() if k in table.c}
                await connection.execute(insert(table).values({**defaults, **values}))

        await migrate(empty_database, "0015")  # 0016 renames the nodes it reads

        def done(outcome: str, started: datetime, finished: datetime) -> dict[str, Any]:
            return {
                "status": "done",
                "outcome": outcome,
                "started_at": started.isoformat(),
                "finished_at": finished.isoformat(),
            }

        waiting = {"status": "waiting", "outcome": None, "started_at": None, "finished_at": None}
        skipped = {**waiting, "status": "skipped"}
        states = await _node_states(empty_database, ids)
        assert states["a"] == {
            "receive": done("ok", NOW, second),
            "ocr": done(
                "ok", second, second + timedelta(seconds=1)
            ),  # the latest run, not the person
            "pages": skipped,
            **dict.fromkeys(
                ("parse", "classify", "extract_fields", "apply_rules", "file"), waiting
            ),
        }
        assert states["b"] == {
            "receive": done("ok", third, third),  # no log entry: the document's updated_at
            "ocr": done("failed", third, third),
            "pages": skipped,
            **dict.fromkeys(
                ("parse", "classify", "extract_fields", "apply_rules", "file"), waiting
            ),
        }
        assert states["c"]["classify"] == done("uncertain", third, third)
        assert (states["c"]["file"], states["c"]["pages"]) == (waiting, skipped)
        assert states["d"]["file"] == done("ok", third, third)
        assert states["d"]["apply_rules"] == done("ok", NOW, second)  # not the rule applied later
        assert states["d"]["pages"] == skipped
        assert all(state["status"] != "waiting" for state in states["d"].values())
        assert [row.processing_status for row in await _document_rows(empty_database)] == [
            "processing",
            "failed",
            "review",
            "completed",
        ]

        # A node a worker is at becomes the current node of the old schema.
        running = {**states["a"]["classify"], "status": "running", "started_at": third.isoformat()}
        async with empty_database.writing() as connection:
            await connection.execute(
                t.documents.update()
                .where(t.documents.c.id == ids["a"])
                .values(processing_nodes={**states["a"], "classify": running})
            )
        documents["a"] = ("processing", "classify", documents["a"][2])

        await downgrade(empty_database, "0009")
        restored = {
            name: (row.processing_node, row.processing_outcomes)
            for name, row in zip(
                documents, await _document_rows(empty_database, old=True), strict=True
            )
        }
        assert restored == {
            name: (current, outcomes) for name, (_, current, outcomes) in documents.items()
        }

    async def test_the_confirmation_is_read_from_the_log(self, empty_database: Database) -> None:
        """Revision 0014 takes the latest confirmation by a person (in the inbox, or a change
        beyond the title) or the source system; a later model run at `classify` undoes it."""
        await migrate(empty_database, "0013")
        ids = {name: new_id() for name in ("user", "space", "inbox", "change", "title", "model")}
        later = NOW + timedelta(minutes=5)
        documents = sa.table(
            "documents",
            *(
                sa.column(c.name, c.type)
                for c in t.documents.c
                if c.name not in {"confirmed_at", "space_id", "topic_id"}
            ),
            sa.column("drawer_id", sa.Uuid()),
        )
        rows: list[tuple[sa.TableClause | sa.Table, dict[str, Any]]] = [
            (t.users, {"id": ids["user"], "username": "a", "username_key": "a", "role": "admin"}),
            (_OLD["drawers"], {"id": ids["space"], "owner_id": ids["user"], "name_key": "d"}),
        ]
        for number, name in enumerate(("inbox", "change", "title", "model")):
            values = {
                "id": ids[name],
                "owner_id": ids["user"],
                "drawer_id": ids["space"],
                "sha256": str(number) * 64,
                "processing_nodes": {},
            }
            rows.append((documents, values))
        log = {"input": {}, "output": {}, "started_at": NOW}
        change = {"changed": ["contact"], "tags_added": [], "tags_removed": []}
        rows += [
            (t.processing_log, {**log, "document_id": ids["inbox"], "node": "classify"}),
            (
                t.processing_log,
                {
                    **log,
                    "document_id": ids["inbox"],
                    "node": "classify",
                    "model_version": "person",
                    "started_at": later,
                },
            ),
            (
                t.processing_log,
                {
                    **log,
                    "document_id": ids["change"],
                    "node": "apply_rules",
                    "model_version": "rules:change",
                    "input": change,
                },
            ),
            (
                t.processing_log,
                {
                    **log,
                    "document_id": ids["title"],
                    "node": "apply_rules",
                    "model_version": "rules:change",
                    "input": {**change, "changed": ["title"]},
                },
            ),
            (
                t.processing_log,
                {
                    **log,
                    "document_id": ids["model"],
                    "node": "classify",
                    "model_version": "imported",
                },
            ),
            (
                t.processing_log,
                {
                    **log,
                    "document_id": ids["model"],
                    "node": "classify",
                    "model_version": "qwen3:8b",
                    "started_at": later,
                },
            ),
        ]
        async with empty_database.writing() as connection:
            for table, values in rows:
                defaults = {k: v for k, v in _DEFAULTS.items() if k in table.c}
                await connection.execute(insert(table).values({**defaults, **values}))

        await migrate(empty_database)
        async with empty_database.reading() as connection:
            found = dict(
                (
                    await connection.execute(select(t.documents.c.id, t.documents.c.confirmed_at))
                ).all()
            )
        assert found == {
            ids["inbox"]: later,
            ids["change"]: NOW,
            ids["title"]: None,
            ids["model"]: None,
        }
        await downgrade(empty_database, "0013")

    async def test_signals_come_before_the_judge_and_back(self, empty_database: Database) -> None:
        """Revision 0016 renames `classify` and `extract_fields` in the node states, the log,
        the jobs and the events; the signals are skipped where the judge ran, waiting where it
        waits, with jobs once the text is parsed. The downgrade restores the old graph."""
        await migrate(empty_database, "0015")
        ids = {name: new_id() for name in ("user", "space", "done", "parsed", "early")}

        def state(status: str, outcome: str | None = None) -> dict[str, Any]:
            return {"status": status, "outcome": outcome, "started_at": None, "finished_at": None}

        ok = state("done", "ok")
        before = ("receive", "ocr", "pages", "parse")
        after = ("classify", "extract_fields", "apply_rules", "file")
        graphs = {
            "done": {**dict.fromkeys(before, ok), **dict.fromkeys(after, ok)},
            "parsed": {**dict.fromkeys(before, ok), **dict.fromkeys(after, state("waiting"))},
            "early": {
                "receive": ok,
                "pages": ok,
                **dict.fromkeys(("ocr", "parse", *after), state("waiting")),
            },
        }
        rows: list[tuple[sa.TableClause | sa.Table, dict[str, Any]]] = [
            (t.users, {"id": ids["user"], "username": "a", "username_key": "a", "role": "admin"}),
            (_OLD["drawers"], {"id": ids["space"], "owner_id": ids["user"], "name_key": "d"}),
        ]
        for number, (name, graph) in enumerate(graphs.items()):
            values = {
                "id": ids[name],
                "owner_id": ids["user"],
                "drawer_id": ids["space"],
                "sha256": str(number) * 64,
                "processing_nodes": graph,
            }
            rows.append((_drawer(_before_0021(t.documents)), values))
        parsed = str(ids["parsed"])
        rows += [
            (
                t.processing_log,
                {"document_id": ids["done"], "node": "classify", "input": {}, "output": {}},
            ),
            (
                t.jobs,
                {
                    "id": new_id(),
                    "kind": "pipeline.node",
                    "payload": {"document_id": parsed, "node": "classify", "run": 1},
                    "dedup_key": f"{parsed}:1:classify",
                    "status": "queued",
                },
            ),
            (
                t.outbox,
                {
                    "event_id": new_id(),
                    "type": "document.node_completed",
                    "payload": {"document_id": parsed, "node": "extract_fields", "run": 1},
                },
            ),
        ]
        async with empty_database.writing() as connection:
            for table, values in rows:
                defaults = {k: v for k, v in _DEFAULTS.items() if k in table.c}
                await connection.execute(insert(table).values({**defaults, **values}))

        async def stored() -> tuple[dict[Any, Any], list[Any], list[Any], list[Any]]:
            async with empty_database.reading() as connection:
                documents = (
                    await connection.execute(
                        select(t.documents.c.id, t.documents.c.processing_nodes)
                    )
                ).all()
                log = (await connection.execute(select(t.processing_log.c.node))).all()
                jobs = (
                    await connection.execute(
                        select(t.jobs.c.dedup_key).where(t.jobs.c.status == "queued")
                    )
                ).all()
                events = (await connection.execute(select(t.outbox.c.payload))).all()
            return (
                {id: nodes for id, nodes in documents},
                [row[0] for row in log],
                sorted(row[0] for row in jobs),
                [row[0]["node"] for row in events],
            )

        await migrate(empty_database)
        nodes, log, jobs, events = await stored()
        assert nodes[ids["done"]]["judge"] == ok and nodes[ids["done"]]["fields"] == ok
        assert {nodes[ids["done"]][signal]["status"] for signal in _SIGNALS} == {"skipped"}
        assert {nodes[ids["parsed"]][signal]["status"] for signal in _SIGNALS} == {"waiting"}
        assert {nodes[ids["early"]][signal]["status"] for signal in _SIGNALS} == {"waiting"}
        assert "classify" not in nodes[ids["parsed"]]
        assert (log, events) == (["judge"], ["fields"])
        assert jobs == sorted(f"{parsed}:1:{signal}" for signal in _SIGNALS)

        await downgrade(empty_database, "0015")
        nodes, log, jobs, events = await stored()
        assert set(nodes[ids["parsed"]]) == set(graphs["parsed"])
        assert nodes[ids["done"]] == graphs["done"]
        assert (log, events, jobs) == (["classify"], ["extract_fields"], [f"{parsed}:1:classify"])

    async def test_rule_templates_become_liquid_and_back(self, empty_database: Database) -> None:
        """Revision 0018 writes the placeholders of title templates in Liquid, doing what they
        did, and keeps text values and reasons that would read as templates as written; the
        downgrade turns them back."""
        await migrate(empty_database, "0017")
        user, rule, text, day = new_id(), new_id(), new_id(), new_id()
        old_actions: list[Any] = [
            {
                "type": "set_title",
                "template": "{contact} Lohn {document_date:YYYY-MM} ({filename})",
            },
            {"type": "set_title", "template": "100% {document_type}"},
            {"type": "force_review", "reason": "check {{x}}"},
            {"type": "set_field", "field_id": str(text), "value": "Nr. {% 1 %}"},
            {"type": "set_field", "field_id": str(day), "value": "2026-01-01"},
            {"type": "add_tags", "tag_ids": []},
        ]
        rows: list[tuple[sa.Table, dict[str, Any]]] = [
            (t.users, {"id": user, "username": "a", "username_key": "a", "role": "admin"}),
            (
                t.field_definitions,
                {"id": text, "name": "Nr", "name_key": "nr", "data_type": "text"},
            ),
            (t.field_definitions, {"id": day, "name": "Tag", "name_key": "tag"}),
            (t.rules, {"id": rule, "scope": "user", "owner_id": user, "current_version": 1}),
            (
                t.rule_versions,
                {"rule_id": rule, "number": 1, "conditions": {"all": []}, "actions": old_actions},
            ),
        ]
        async with empty_database.writing() as connection:
            for table, values in rows:
                defaults = {k: v for k, v in _DEFAULTS.items() if k in table.c}
                await connection.execute(insert(table).values({**defaults, **values}))

        async def stored() -> list[Any]:
            async with empty_database.reading() as connection:
                return list((await connection.execute(select(t.rule_versions.c.actions))).one()[0])

        await migrate(empty_database)
        actions = await stored()
        title = actions[0]["template"]
        assert title == (
            "{{ contact | default: '' }} Lohn "
            "{% if document_date %}{{ document_date | date: '%Y-%m' }}{% endif %} ({{ filename }})"
        )
        assert actions[1]["template"] == "100% {{ document_type | default: '' }}"
        assert actions[2]["reason"] == "{% raw %}check {{x}}{% endraw %}"
        assert actions[3]["value"] == "{% raw %}Nr. {% 1 %}{% endraw %}"
        assert actions[4:] == old_actions[4:]
        # As before: a missing value leaves its gap, the date is written as it was.
        values = {"filename": "scan", "document_date": date(2026, 3, 31)}
        assert " ".join(builders.TEMPLATES.render(title, values).split()) == "Lohn 2026-03 (scan)"
        assert builders.TEMPLATES.render(actions[2]["reason"], {}) == "check {{x}}"

        await downgrade(empty_database, "0017")
        assert await stored() == old_actions

    async def test_title_patterns_become_rules_of_their_types_and_back(
        self, empty_database: Database
    ) -> None:
        """Revision 0019 writes title patterns in Liquid (a field by its key) and gives each
        type with one a global rule that sets the title; the downgrade removes the rules and
        writes the old placeholders again."""
        await migrate(empty_database, "0018")
        payslip, letter, field = new_id(), new_id(), new_id()
        old = "Lohn {contact} {document_date:YYYY-MM} {field:LA Brutto}"
        rows: list[tuple[sa.Table, dict[str, Any]]] = [
            (
                t.document_types,
                {"id": payslip, "name": "Lohn", "name_key": "lohn", "title_pattern": old},
            ),
            (t.document_types, {"id": letter, "name": "Brief", "name_key": "brief"}),
            (
                t.field_definitions,
                {"id": field, "name": "LA Brutto", "name_key": "la brutto", "data_type": "amount"},
            ),
        ]
        async with empty_database.writing() as connection:
            for table, values in rows:
                defaults = {k: v for k, v in _DEFAULTS.items() if k in table.c}
                await connection.execute(insert(table).values({**defaults, **values}))

        await migrate(empty_database)
        async with empty_database.reading() as connection:
            patterns = dict(
                (
                    await connection.execute(
                        select(t.document_types.c.id, t.document_types.c.title_pattern)
                    )
                ).all()
            )
            (link,) = (await connection.execute(select(t.title_pattern_rules))).all()
            (version,) = (await connection.execute(select(t.rule_versions))).mappings().all()
            (rule,) = (await connection.execute(select(t.rules))).mappings().all()
        new = "Lohn {{ contact }} {{ document_date | date: '%Y-%m' }} {{ field.la_brutto }}"
        assert patterns == {payslip: new, letter: None}
        assert link.document_type_id == payslip and link.rule_id == rule["id"]
        assert (rule["scope"], rule["owner_id"], rule["enabled"]) == ("global", None, True)
        assert version["name"] == "Lohn" and version["actions"] == [
            {"type": "set_title", "template": new}
        ]
        assert version["conditions"] == {
            "all": [{"field": "document_type", "op": "is", "value": str(payslip)}]
        }
        uow = SqlUnitOfWorkFactory(empty_database)
        async with uow() as unit:
            loaded = await unit.rules.get(rule["id"])
        assert loaded.managed_by == payslip and loaded.definition.actions[0].type == "set_title"

        await downgrade(empty_database, "0018")
        async with empty_database.reading() as connection:
            restored = (
                await connection.execute(
                    select(t.document_types.c.title_pattern).where(t.document_types.c.id == payslip)
                )
            ).scalar_one()
            remaining = (
                await connection.execute(select(sa.func.count()).select_from(t.rules))
            ).scalar_one()
        assert (restored, remaining) == (old, 0)

    async def test_two_fields_with_one_key_stop_revision_0019(
        self, empty_database: Database
    ) -> None:
        await migrate(empty_database, "0018")
        async with empty_database.writing() as connection:
            for name in ("LA Brutto", "LA_Brutto"):
                values = {"id": new_id(), "name": name, "name_key": name.casefold()}
                defaults = {k: v for k, v in _DEFAULTS.items() if k in t.field_definitions.c}
                await connection.execute(insert(t.field_definitions).values({**defaults, **values}))
        with pytest.raises(RuntimeError, match=r"would both be field\.la_brutto"):
            await migrate(empty_database)

    async def test_titles_get_their_sort_key_and_back(self, empty_database: Database) -> None:
        """Revision 0023 fills the sort key of every title, case and accents aside."""
        await migrate(empty_database, "0022")
        ids = {name: new_id() for name in ("user", "space", "letter", "invoice")}
        documents = _before_0021(t.documents)
        rows: list[tuple[sa.TableClause | sa.Table, dict[str, Any]]] = [
            (t.users, {"id": ids["user"], "username": "a", "username_key": "a", "role": "admin"}),
            (_OLD["drawers"], {"id": ids["space"], "owner_id": ids["user"], "name_key": "d"}),
        ]
        for number, (name, title) in enumerate(
            (("letter", "Ärztebrief Straße 5"), ("invoice", " INVOICE "))
        ):
            values = {
                "id": ids[name],
                "owner_id": ids["user"],
                "drawer_id": ids["space"],
                "sha256": str(number) * 64,
                "title": title,
                "processing_nodes": {},
                "processing_vision": False,
            }
            rows.append((_drawer(documents), values))
        async with empty_database.writing() as connection:
            for table, values in rows:
                defaults = {k: v for k, v in _DEFAULTS.items() if k in table.c}
                columns = {k: v for k, v in {**defaults, **values}.items() if k in table.c}
                await connection.execute(insert(table).values(columns))

        await migrate(empty_database)
        async with empty_database.reading() as connection:
            keys = dict(
                (await connection.execute(select(t.documents.c.id, t.documents.c.title_key))).all()
            )
        assert keys == {ids["letter"]: "arztebrief strasse 5", ids["invoice"]: "invoice"}
        await downgrade(empty_database, "0022")
        async with empty_database.reading() as connection:
            assert len((await connection.execute(select(documents.c.id))).all()) == 2

    async def test_drawers_become_spaces_and_back(self, empty_database: Database) -> None:
        """Revision 0024 renames the tables and the names Papiq stores; the rows that refer to
        a document stay, and so does text a person wrote. The downgrade restores all of it."""
        await migrate(empty_database, "0023")
        async with empty_database.reading() as connection:
            schema = await connection.run_sync(_schema)
        ids = {
            name: new_id()
            for name in ("user", "other", "default", "shared", "document", "type", "rule")
        }
        ids |= {name: new_id() for name in ("text", "number", "tag")}
        user, shared, document = str(ids["user"]), str(ids["shared"]), str(ids["document"])
        template = (
            "{{ drawer }}: drawer {% if drawer %}{{ drawer | upcase }}{% endif %}"
            " {% raw %}{{ drawer }}{% endraw %} {{ 'drawer' }} {{ field.drawer }}"
        )
        renamed = (
            "{{ space }}: drawer {% if space %}{{ space | upcase }}{% endif %}"
            " {% raw %}{{ drawer }}{% endraw %} {{ 'drawer' }} {{ field.drawer }}"
        )
        actions = [
            {"type": "set_drawer", "drawer_id": shared},
            {"type": "set_title", "template": template},
            {"type": "set_field", "field_id": str(ids["text"]), "value": "{{ drawer }}"},
            {"type": "set_field", "field_id": str(ids["number"]), "value": "{{ drawer }}"},
            {"type": "force_review", "reason": "Check {{ drawer }}"},
        ]
        log: dict[str, tuple[str | None, dict[str, Any], dict[str, Any]]] = {
            "person:drawer": (
                "person:drawer",
                {"trigger": "change", "actor": user, "drawer_id": shared},
                {"changed": ["drawer"]},
            ),
            "person": (
                "person",
                {"actor": user},
                {
                    "drawer_id": shared,
                    "changed": ["drawer", "title"],
                    "accepted": ["drawer"],
                    "entered": [],
                    "kept": ["drawer"],
                },
            ),
            "file": (None, {}, {"fields": [{"field": "drawer", "reason": "the drawer stays"}]}),
            "apply_rules": (
                None,
                {},
                {
                    "fields": [{"field": "drawer", "outcome": "ok"}],
                    "rules": [
                        {
                            "applied": [{"field": "drawer", "value": shared}],
                            "notes": [{"field": "drawer", "message": "drawer 'x' is gone"}],
                        }
                    ],
                },
            ),
        }
        rows: list[tuple[sa.TableClause | sa.Table, dict[str, Any]]] = [
            (t.users, {"id": ids["user"], "username": "a", "username_key": "a", "role": "admin"}),
            (t.users, {"id": ids["other"], "username": "b", "username_key": "b", "role": "user"}),
            (_OLD["drawers"], {"id": ids["default"], "owner_id": ids["user"], "name_key": "d"}),
            (
                _OLD["drawers"],
                {
                    "id": ids["shared"],
                    "owner_id": ids["user"],
                    "name": "Shared",
                    "name_key": "shared",
                    "is_default": False,
                },
            ),
            (
                _OLD["shares"],
                {"drawer_id": ids["shared"], "user_id": ids["other"], "level": "read"},
            ),
            (
                _drawer(_before_0021(t.documents)),
                {"id": ids["document"], "owner_id": ids["user"], "drawer_id": ids["shared"]},
            ),
            (t.tags, {"id": ids["tag"], "name": "T", "name_key": "t"}),
            (t.document_tags, {"document_id": ids["document"], "tag_id": ids["tag"]}),
            (
                t.field_definitions,
                {"id": ids["text"], "name": "A", "name_key": "a", "data_type": "text"},
            ),
            (
                t.field_definitions,
                {"id": ids["number"], "name": "B", "name_key": "b", "data_type": "number"},
            ),
            (
                t.document_fields,
                {"document_id": ids["document"], "field_id": ids["text"], "kind": "text"},
            ),
            (
                t.document_types,
                {"id": ids["type"], "name": "L", "name_key": "l", "title_pattern": template},
            ),
            (
                t.rules,
                {"id": ids["rule"], "scope": "user", "owner_id": ids["user"], "current_version": 1},
            ),
            (
                t.rule_versions,
                {"rule_id": ids["rule"], "number": 1, "conditions": {}, "actions": actions},
            ),
            (
                t.outbox,
                {
                    "event_id": new_id(),
                    "type": "document.filed",
                    "payload": {"document_id": document, "drawer_id": shared},
                },
            ),
        ]
        rows += [
            (
                t.processing_log,
                {
                    "document_id": ids["document"],
                    "node": node,
                    "model_version": version,
                    "input": input_,
                    "output": output,
                },
            )
            for node, (version, input_, output) in log.items()
        ]
        async with empty_database.writing() as connection:
            for table, values in rows:
                defaults = {k: v for k, v in _DEFAULTS.items() if k in table.c}
                await connection.execute(insert(table).values({**defaults, **values}))

        async def stored(old: bool) -> dict[str, Any]:
            spaces = _OLD["drawers"] if old else t.spaces
            shares = _OLD["shares"] if old else t.space_shares
            documents = _drawer(_before_0021(t.documents)) if old else t.documents
            space_column = documents.c.drawer_id if old else documents.c.space_id
            async with empty_database.reading() as connection:

                async def all_(query: Any) -> list[Any]:
                    return [tuple(row) for row in (await connection.execute(query)).all()]

                return {
                    "spaces": sorted(await all_(select(spaces.c.id, spaces.c.is_default))),
                    "shares": await all_(select(shares)),
                    "document": await all_(select(documents.c.id, space_column)),
                    "tags": await all_(select(t.document_tags)),
                    "fields": await all_(select(t.document_fields.c.field_id)),
                    "pattern": await all_(select(t.document_types.c.title_pattern)),
                    "actions": await all_(select(t.rule_versions.c.actions)),
                    "event": await all_(select(t.outbox.c.payload)),
                    "log": {
                        row.node: (row.model_version, row.input, row.output)
                        for row in (await connection.execute(select(t.processing_log))).all()
                    },
                }

        before = await stored(old=True)
        await migrate(empty_database)
        after = await stored(old=False)
        assert after["spaces"] == before["spaces"]
        assert after["shares"] == [(ids["shared"], ids["other"], "read")]
        assert after["document"] == [(ids["document"], ids["shared"])]
        assert (after["tags"], after["fields"]) == (before["tags"], before["fields"])
        assert after["pattern"] == [(renamed,)]
        assert after["actions"] == [
            (
                [
                    {"type": "set_space", "space_id": shared},
                    {"type": "set_title", "template": renamed},
                    {"type": "set_field", "field_id": str(ids["text"]), "value": "{{ space }}"},
                    {"type": "set_field", "field_id": str(ids["number"]), "value": "{{ drawer }}"},
                    {"type": "force_review", "reason": "Check {{ space }}"},
                ],
            )
        ]
        assert after["event"] == [({"document_id": document, "space_id": shared},)]
        assert after["log"] == {
            "person:drawer": (
                "person:space",
                {"trigger": "change", "actor": user, "space_id": shared},
                {"changed": ["space"]},
            ),
            "person": (
                "person",
                {"actor": user},
                {
                    "space_id": shared,
                    "changed": ["space", "title"],
                    "accepted": ["space"],
                    "entered": [],
                    "kept": ["space"],
                },
            ),
            "file": (None, {}, {"fields": [{"field": "space", "reason": "the drawer stays"}]}),
            "apply_rules": (
                None,
                {},
                {
                    "fields": [{"field": "space", "outcome": "ok"}],
                    "rules": [
                        {
                            "applied": [{"field": "space", "value": shared}],
                            "notes": [{"field": "space", "message": "drawer 'x' is gone"}],
                        }
                    ],
                },
            ),
        }

        await downgrade(empty_database, "0023")
        assert await stored(old=True) == before
        async with empty_database.reading() as connection:
            assert await connection.run_sync(_schema) == schema

    async def test_a_template_that_uses_space_stops_revision_0024(
        self, empty_database: Database
    ) -> None:
        await migrate(empty_database, "0023")
        pattern = "{% for space in tags %}{{ drawer }}{% endfor %}"
        async with empty_database.writing() as connection:
            values = {"id": new_id(), "name": "L", "name_key": "l", "title_pattern": pattern}
            defaults = {k: v for k, v in _DEFAULTS.items() if k in t.document_types.c}
            await connection.execute(insert(t.document_types).values({**defaults, **values}))
        with pytest.raises(RuntimeError, match=r"uses 'space' already"):
            await migrate(empty_database)

    async def test_tags_get_scopes_and_groups_and_back(self, empty_database: Database) -> None:
        """Revision 0026 keeps every tag (a document tag, proposed, shown, at the top) and every
        row that refers to one; the downgrade drops the groups and the nesting, and the schema
        is that of 0025 again."""
        await migrate(empty_database, "0025")
        async with empty_database.reading() as connection:
            schema = await connection.run_sync(_schema)
        ids = {name: new_id() for name in ("user", "space", "first", "second", "tax", "paid")}
        ids |= {name: new_id() for name in ("contact", "type", "group", "type_group", "child")}
        rows: list[tuple[sa.TableClause | sa.Table, dict[str, Any]]] = [
            (t.users, {"id": ids["user"], "username": "a", "username_key": "a", "role": "admin"}),
            (t.spaces, {"id": ids["space"], "owner_id": ids["user"], "name_key": "d"}),
            (t.tags, {"id": ids["tax"], "name": "Tax", "name_key": "tax"}),
            (t.tags, {"id": ids["paid"], "name": "Paid", "name_key": "paid"}),
            (t.contacts, {"id": ids["contact"], "name": "Nord", "name_key": "nord"}),
            (t.document_types, {"id": ids["type"], "name": "Invoice", "name_key": "invoice"}),
        ]
        for number, document in enumerate(("first", "second")):
            values = {"id": ids[document], "owner_id": ids["user"], "space_id": ids["space"]}
            rows.append((t.documents, values | {"sha256": str(number) * 64}))
        rows += [
            (t.document_tags, {"document_id": ids["first"], "tag_id": ids["tax"]}),
            (t.document_tags, {"document_id": ids["first"], "tag_id": ids["paid"]}),
            (t.document_tags, {"document_id": ids["second"], "tag_id": ids["tax"]}),
        ]
        async with empty_database.writing() as connection:
            for table, values in rows:
                defaults = {k: v for k, v in _DEFAULTS.items() if k in table.c}
                await connection.execute(insert(table).values({**defaults, **values}))
        flat = sa.table(
            "tags",
            sa.column("id", sa.Uuid()),
            sa.column("name", sa.Text()),
            sa.column("name_key", sa.Text()),
            sa.column("created_at", UtcDateTime()),
        )

        async def stored(table: sa.TableClause | sa.Table) -> dict[str, Any]:
            async with empty_database.reading() as connection:

                async def all_(query: Any) -> list[tuple[Any, ...]]:
                    return sorted(tuple(row) for row in (await connection.execute(query)).all())

                return {
                    "tags": await all_(select(table)),
                    "document_tags": await all_(select(t.document_tags)),
                }

        before = await stored(flat)
        await migrate(empty_database)
        after = await stored(t.tags)
        assert after["document_tags"] == before["document_tags"]
        assert {row[:4] for row in after["tags"]} == {
            (ids["paid"], "Paid", "paid", "document"),
            (ids["tax"], "Tax", "tax", "document"),
        }
        assert {row[4:7] for row in after["tags"]} == {(None, "auto", True)}

        # Groups, a contact group named like a document tag, and a nested tag.
        new_rows: list[tuple[sa.Table, dict[str, Any]]] = [
            (t.tags, {"id": ids["group"], "name": "Tax", "name_key": "tax", "scope": "contact"}),
            (
                t.tags,
                {"id": ids["type_group"], "name": "B", "name_key": "b", "scope": "document_type"},
            ),
            (t.tags, {"id": ids["child"], "name": "C", "name_key": "c", "parent_id": ids["tax"]}),
            (t.contact_groups, {"contact_id": ids["contact"], "tag_id": ids["group"]}),
            (
                t.document_type_groups,
                {"document_type_id": ids["type"], "tag_id": ids["type_group"]},
            ),
            (t.document_tags, {"document_id": ids["second"], "tag_id": ids["child"]}),
        ]
        async with empty_database.writing() as connection:
            for table, values in new_rows:
                defaults = {k: v for k, v in _DEFAULTS.items() if k in table.c}
                await connection.execute(insert(table).values({**defaults, **values}))
        with pytest.raises(sa.exc.IntegrityError):
            async with empty_database.writing() as connection:
                same: dict[str, Any] = {"name": "tax", "name_key": "tax", "scope": "contact"}
                defaults = {k: v for k, v in _DEFAULTS.items() if k in t.tags.c}
                await connection.execute(
                    insert(t.tags).values({**defaults, **same, "id": new_id()})
                )

        await downgrade(empty_database, "0025")
        child = (ids["child"], "C", "c", NOW)
        assert await stored(flat) == {
            "tags": sorted([*before["tags"], child]),
            "document_tags": sorted([*before["document_tags"], (ids["second"], ids["child"])]),
        }
        async with empty_database.reading() as connection:
            assert await connection.run_sync(_schema) == schema

    async def test_topics_come_and_go_with_revision_0027(self, empty_database: Database) -> None:
        """Revision 0027 keeps every document, identifier and the rows that refer to them,
        without a topic; the downgrade drops the topics and the columns again, and the schema is
        that of 0026."""
        await migrate(empty_database, "0026")
        async with empty_database.reading() as connection:
            schema = await connection.run_sync(_schema)
        ids = {name: new_id() for name in ("user", "space", "first", "second", "tax", "contact")}
        ids |= {name: new_id() for name in ("insurance", "health", "type")}
        rows: list[tuple[sa.TableClause | sa.Table, dict[str, Any]]] = [
            (t.users, {"id": ids["user"], "username": "a", "username_key": "a", "role": "admin"}),
            (t.spaces, {"id": ids["space"], "owner_id": ids["user"], "name_key": "d"}),
            (t.tags, {"id": ids["tax"], "name": "Tax", "name_key": "tax"}),
            (t.contacts, {"id": ids["contact"], "name": "Nord", "name_key": "nord"}),
            (t.document_types, {"id": ids["type"], "name": "Invoice", "name_key": "invoice"}),
        ]
        for number, document in enumerate(("first", "second")):
            values = {"id": ids[document], "owner_id": ids["user"], "space_id": ids["space"]}
            values |= {"contact_id": ids["contact"], "document_type_id": ids["type"]}
            rows.append((t.documents, values | {"sha256": str(number) * 64, "title": document}))
        rows += [
            (t.document_tags, {"document_id": ids["first"], "tag_id": ids["tax"]}),
            (t.document_identifiers, {"document_id": ids["first"], "kind": "iban", "value": "X"}),
            (
                t.contact_identifiers,
                {"contact_id": ids["contact"], "kind": "iban", "value": "X", "source": "manual"},
            ),
            (
                t.contact_identifiers,
                {"contact_id": ids["contact"], "kind": "vat_id", "value": "Y", "source": "learned"},
            ),
        ]
        async with empty_database.writing() as connection:
            for table, values in rows:
                defaults = {k: v for k, v in _DEFAULTS.items() if k in table.c}
                await connection.execute(insert(table).values({**defaults, **values}))
        documents = sa.table(
            "documents",
            *(sa.column(name) for name in ("id", "owner_id", "space_id", "sha256", "title")),
            *(sa.column(name) for name in ("contact_id", "document_type_id", "processing_nodes")),
        )
        identifiers = sa.table(
            "contact_identifiers",
            *(sa.column(c) for c in ("contact_id", "kind", "value", "source")),
        )

        async def stored() -> dict[str, list[tuple[Any, ...]]]:
            async with empty_database.reading() as connection:

                async def all_(query: Any) -> list[tuple[Any, ...]]:
                    return sorted(
                        tuple(str(value) for value in row)
                        for row in (await connection.execute(query)).all()
                    )

                return {
                    "documents": await all_(select(documents)),
                    "identifiers": await all_(select(identifiers)),
                    "document_tags": await all_(select(t.document_tags)),
                    "document_identifiers": await all_(select(t.document_identifiers)),
                    "tags": await all_(select(t.tags.c.id, t.tags.c.scope)),
                }

        before = await stored()
        await migrate(empty_database)
        assert await stored() == before
        async with empty_database.reading() as connection:
            topics = await connection.execute(select(t.documents.c.topic_id))
            assert {row[0] for row in topics} == {None}
            topics = await connection.execute(select(t.contact_identifiers.c.topic_id))
            assert {row[0] for row in topics} == {None}

        topic: dict[str, Any] = {"scope": "topic"}
        new_rows: list[tuple[sa.Table, dict[str, Any]]] = [
            (t.tags, {"id": ids["insurance"], "name": "Tax", "name_key": "tax", **topic}),
            (
                t.tags,
                {"id": ids["health"], "name": "H", "name_key": "h", "parent_id": ids["insurance"]}
                | topic,
            ),
            (t.contact_topics, {"contact_id": ids["contact"], "tag_id": ids["health"]}),
        ]
        async with empty_database.writing() as connection:
            for table, values in new_rows:
                defaults = {k: v for k, v in _DEFAULTS.items() if k in table.c}
                await connection.execute(insert(table).values({**defaults, **values}))
            await connection.execute(
                t.documents.update()
                .where(t.documents.c.id == ids["first"])
                .values(topic_id=ids["health"])
            )
            await connection.execute(
                t.contact_identifiers.update()
                .where(t.contact_identifiers.c.kind == "iban")
                .values(topic_id=ids["health"])
            )
        with pytest.raises(sa.exc.IntegrityError):
            async with empty_database.writing() as connection:
                await connection.execute(
                    t.documents.update()
                    .where(t.documents.c.id == ids["second"])
                    .values(topic_id=new_id())
                )

        await downgrade(empty_database, "0026")
        assert await stored() == before
        async with empty_database.reading() as connection:
            assert await connection.run_sync(_schema) == schema

    async def test_icons_colours_and_logos_come_and_go_with_revision_0028(
        self, empty_database: Database
    ) -> None:
        """Revision 0028 keeps every tag, document type and contact as it is, without icon,
        colour or logo; the downgrade drops them again, and the schema is that of 0027."""
        await migrate(empty_database, "0027")
        async with empty_database.reading() as connection:
            schema = await connection.run_sync(_schema)
        ids = {name: new_id() for name in ("tag", "topic", "type", "contact", "other")}
        rows: list[tuple[sa.Table, dict[str, Any]]] = [
            (t.tags, {"id": ids["tag"], "name": "Tax", "name_key": "tax"}),
            (t.tags, {"id": ids["topic"], "name": "Car", "name_key": "car", "scope": "topic"}),
            (t.document_types, {"id": ids["type"], "name": "Invoice", "name_key": "invoice"}),
            (t.contacts, {"id": ids["contact"], "name": "Nord", "name_key": "nord"}),
            (t.contacts, {"id": ids["other"], "name": "Süd", "name_key": "süd"}),
            (t.contact_topics, {"contact_id": ids["contact"], "tag_id": ids["topic"]}),
        ]
        async with empty_database.writing() as connection:
            for table, values in rows:
                defaults = {k: v for k, v in _DEFAULTS.items() if k in table.c}
                await connection.execute(insert(table).values({**defaults, **values}))
        columns = ("id", "name", "name_key", "created_at", "version")
        tables = {
            name: sa.table(name, *(sa.column(column) for column in columns))
            for name in ("tags", "document_types", "contacts")
        }

        async def stored() -> dict[str, list[tuple[Any, ...]]]:
            async with empty_database.reading() as connection:
                found = {
                    name: sorted(
                        tuple(str(value) for value in row)
                        for row in (await connection.execute(select(table))).all()
                    )
                    for name, table in tables.items()
                }
                found["contact_topics"] = sorted(
                    tuple(map(str, row))
                    for row in (await connection.execute(select(t.contact_topics))).all()
                )
                return found

        before = await stored()
        await migrate(empty_database)
        assert await stored() == before
        looks = (
            t.tags.c.icon,
            t.tags.c.color,
            t.document_types.c.icon,
            t.contacts.c.logo_source,
            t.contacts.c.logo_failed_domains,
        )
        async with empty_database.reading() as connection:
            for column in looks:
                assert {row[0] for row in await connection.execute(select(column))} == {None}
        async with empty_database.writing() as connection:
            await connection.execute(
                t.tags.update()
                .where(t.tags.c.id == ids["tag"])
                .values(icon="calculator", color="#b45309")
            )
            await connection.execute(t.document_types.update().values(icon="receipt"))
            await connection.execute(
                t.contacts.update()
                .where(t.contacts.c.id == ids["contact"])
                .values(logo_source="fetched", logo_domain="nord.example", logo_stored_at=NOW)
            )
            await connection.execute(
                t.contacts.update()
                .where(t.contacts.c.id == ids["other"])
                .values(logo_failed_domains="sued.example a.example", logo_failed_at=NOW)
            )

        await downgrade(empty_database, "0027")
        assert await stored() == before
        async with empty_database.reading() as connection:
            assert await connection.run_sync(_schema) == schema

    async def test_migrations_match_the_table_definitions(
        self, empty_database: Database, model_database: Database
    ) -> None:
        """Alembic's comparison, plus the DDL as the database reports it: Alembic does not
        compare partial-index predicates or SQLite's AUTOINCREMENT."""
        await migrate(empty_database)
        async with empty_database.reading() as connection:
            differences = await connection.run_sync(_compare)
            migrated = await connection.run_sync(_schema)
        assert differences == []
        async with model_database.writing() as connection:
            await connection.run_sync(t.metadata.create_all)
        async with model_database.reading() as connection:
            modelled = await connection.run_sync(_schema)
        assert migrated == modelled


_OLD = {
    "drawers": sa.table(
        "drawers",
        sa.column("id", sa.Uuid()),
        sa.column("owner_id", sa.Uuid()),
        sa.column("name", sa.Text()),
        sa.column("name_key", sa.Text()),
        sa.column("is_default", sa.Boolean()),
        sa.column("created_at", UtcDateTime()),
        sa.column("version", sa.Integer()),
    ),
    "shares": sa.table(
        "drawer_shares",
        sa.column("drawer_id", sa.Uuid()),
        sa.column("user_id", sa.Uuid()),
        sa.column("level", sa.Text()),
    ),
    "definitions": sa.table(
        "attribute_definitions",
        sa.column("id", sa.Uuid()),
        sa.column("name", sa.Text()),
        sa.column("name_key", sa.Text()),
        sa.column("data_type", sa.Text()),
        sa.column("is_global", sa.Boolean()),
        sa.column("choices", json_type()),
        sa.column("created_at", UtcDateTime()),
        sa.column("version", sa.Integer()),
    ),
    "types": sa.table(
        "attribute_document_types",
        sa.column("attribute_id", sa.Uuid()),
        sa.column("document_type_id", sa.Uuid()),
    ),
    "values": sa.table(
        "document_attributes",
        sa.column("document_id", sa.Uuid()),
        sa.column("attribute_id", sa.Uuid()),
        sa.column("kind", sa.Text()),
        sa.column("value_text", sa.Text()),
    ),
}
_SIGNALS = ("signal_llm", "signal_classifier", "signal_neighbours", "signal_facts")
_OK_UP_TO_CLASSIFY = {"receive": "ok", "ocr": "ok", "parse": "ok", "classify": "ok"}

_DEFAULTS: dict[str, Any] = {
    "created_at": NOW,
    "updated_at": NOW,
    "occurred_at": NOW,
    "recorded_at": NOW,
    "started_at": NOW,
    "run_at": NOW,
    "version": 1,
    "is_default": True,
    "data_type": "date",
    "is_global": False,
    "choices": [],
    "kind": "text",
    "value_text": "Monday",
    "sha256": "0" * 64,
    "title": "T",
    "original_filename": "t.pdf",
    "media_type": "application/pdf",
    "channel": "api",
    "processing_status": "processing",
    "processing_run": 1,
    "processing_outcomes": {},
    "run": 1,
    "outcome": "ok",
    "pipeline_version": "1",
    "duration_us": 1,
    "enabled": True,
    "name": "N",
    "priority": 1,
    "triggers": ["ingest"],
    "status": "queued",
    "attempts": 0,
    "releases": 0,
}


async def _stored(database: Database, *, old: bool) -> dict[str, Any]:
    """The rows of `test_attributes_become_fields_and_back`, under the old or new names."""
    prefix = "attribute" if old else "field"
    id = sa.column(f"{prefix}_id", sa.Uuid())
    definitions = sa.table(
        f"{prefix}_definitions",
        sa.column("id", sa.Uuid()),
        sa.column("name", sa.Text()),
        sa.column("data_type", sa.Text()),
    )
    types = sa.table(f"{prefix}_document_types", id, sa.column("document_type_id", sa.Uuid()))
    values = sa.table(
        "document_attributes" if old else "document_fields",
        sa.column("document_id", sa.Uuid()),
        sa.column(id.name, sa.Uuid()),
        sa.column("kind", sa.Text()),
        sa.column("value_text", sa.Text()),
    )
    async with database.reading() as connection:

        async def one(table: Any) -> Any:
            return (await connection.execute(select(table))).one()

        definition, scope, value = await one(definitions), await one(types), await one(values)
        documents = _before_0009(t.documents) if old else _before_0021(t.documents)
        log_table = _before_0009(t.processing_log) if old else t.processing_log
        document, log = await one(documents), await one(log_table)
        rule, job, event = await one(t.rule_versions), await one(t.jobs), await one(t.outbox)
    return {
        "definition": tuple(definition),
        "scope": tuple(scope),
        "value": tuple(value),
        "document": (
            (document.processing_step, document.processing_outcomes)
            if old
            else _current_and_outcomes(document.processing_nodes)
        ),
        "log": (log.step if old else log.node, log.output),
        "rule": (rule.conditions, rule.actions),
        "job": (job.payload, job.dedup_key),
        "event": event.payload,
    }


async def _node_names(database: Database, *, old: bool) -> dict[str, Any]:
    """The rows of `test_steps_become_nodes_and_back`, under the old or new names."""
    documents = _before_0009(t.documents) if old else _before_0021(t.documents)
    log_table = _before_0009(t.processing_log) if old else t.processing_log
    async with database.reading() as connection:

        async def one(table: Any) -> Any:
            return (await connection.execute(select(table))).one()

        document, log = await one(documents), await one(log_table)
        job, event = await one(t.jobs), await one(t.outbox)
        webhook, delivery = await one(t.webhooks), await one(t.webhook_deliveries)
    return {
        "document": (
            document.processing_step if old else _current_and_outcomes(document.processing_nodes)[0]
        ),
        "log": log.step if old else log.node,
        "job": (job.kind, job.payload),
        "event": (event.type, event.payload),
        "webhook": webhook.event_types,
        "delivery": delivery.event_type,
    }


async def _node_states(database: Database, ids: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """`processing_nodes` of the documents of `ids`, by name."""
    by_id = {id: name for name, id in ids.items()}
    return {
        by_id[row.id]: row.processing_nodes
        for row in await _document_rows(database)
        if row.id in by_id
    }


async def _document_rows(database: Database, *, old: bool = False) -> list[Any]:
    table = _before_0010(t.documents) if old else _before_0021(t.documents)
    async with database.reading() as connection:
        return list((await connection.execute(select(table).order_by(table.c.sha256))).all())


def _drawer(table: sa.TableClause) -> sa.TableClause:
    """`table` (documents) with the space column as the revisions before 0024 named it, for
    rows written there; the other helpers leave the column out, so they read at any revision."""
    columns = [sa.column(c.name, c.type) for c in table.c]
    return sa.table(table.name, *columns, sa.column("drawer_id", sa.Uuid()))


def _before_0021(table: sa.Table) -> sa.TableClause:
    """`table` as the revisions before 0021 had it: no vision reading of the run, no sort key
    of the title (0023) and no topic (0027); without the space column (see `_drawer`)."""
    later = {"processing_vision", "title_key", "space_id", "topic_id"}
    return sa.table(
        table.name, *(sa.column(c.name, c.type) for c in table.c if c.name not in later)
    )


def _before_0010(table: sa.Table) -> sa.TableClause:
    """`table` as the revisions before 0010 had it: the current node and the outcomes instead
    of the state of every node, and no page count (0011), confirmation (0014), vision reading
    (0021), sort key of the title (0023) or topic (0027); without the space column (see
    `_drawer`)."""
    later = {
        "processing_nodes",
        "page_count",
        "confirmed_at",
        "processing_vision",
        "title_key",
        "space_id",
        "topic_id",
    }
    columns = [sa.column(c.name, c.type) for c in table.c if c.name not in later]
    if table.name == "documents":
        columns.append(sa.column("processing_node", sa.Text()))
        columns.append(sa.column("processing_outcomes", json_type()))
    return sa.table(table.name, *columns)


def _before_0009(table: sa.Table) -> sa.TableClause:
    """`table` as the revisions before 0009 had it: the node columns under their old names."""
    old = {"processing_node": "processing_step", "node": "step"}
    before = _before_0010(table)
    return sa.table(table.name, *(sa.column(old.get(c.name, c.name), c.type) for c in before.c))


def _current_and_outcomes(nodes: dict[str, Any]) -> tuple[str | None, dict[str, str]]:
    """The current node and the outcomes as the revisions before 0010 stored them, derived
    from the state of every node (the way the downgrade does it). In graph order: Postgres
    keeps the keys of a JSONB object shortest first, not as they were written."""
    waiting = [node.value for node in NODES if nodes.get(node.value, {}).get("status") == "waiting"]
    outcomes = {
        name: state["outcome"]
        for name, state in nodes.items()
        if name != "pages" and state["outcome"] is not None
    }
    return (waiting[0] if waiting else None), outcomes


def _compare(connection: Connection) -> list[Any]:
    context = MigrationContext.configure(connection, opts={"compare_type": True})
    return list(compare_metadata(context, t.metadata))


def _schema(connection: Connection) -> set[tuple[Any, ...]]:
    """Tables, columns, constraints and indexes as the database describes them."""
    if connection.dialect.name == "sqlite":
        query = (
            "SELECT type, name, sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' AND name NOT LIKE '%alembic_version%'"
        )
        return {
            (kind, name, _table_clauses(sql) if kind == "table" else " ".join(sql.split()))
            for kind, name, sql in connection.exec_driver_sql(query)
        }
    queries = [
        "SELECT 'index', indexname, indexdef FROM pg_indexes WHERE schemaname = 'public'",
        "SELECT 'constraint', conname, pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE connamespace = 'public'::regnamespace",
        "SELECT 'column', table_name || '.' || column_name, "
        "concat_ws(' ', data_type, numeric_precision, numeric_scale, character_maximum_length, "
        "is_nullable, column_default) FROM information_schema.columns "
        "WHERE table_schema = 'public'",
    ]
    return {
        tuple(row)
        for query in queries
        for row in connection.exec_driver_sql(query)
        if "alembic_version" not in row[1]
    }


def _table_clauses(sql: str) -> tuple[str, frozenset[str]]:
    """CREATE TABLE as its head and its clauses (split at top-level commas), order ignored."""
    head, body = " ".join(sql.split()).split("(", 1)
    clauses, depth, current = [], 0, ""
    for char in body.rsplit(")", 1)[0]:
        depth += {"(": 1, ")": -1}.get(char, 0)
        if char == "," and depth == 0:
            clauses.append(current.strip())
            current = ""
        else:
            current += char
    return head.strip(), frozenset([*clauses, current.strip()])


async def _seed_user(uow_factory: UnitOfWorkFactory, user: User) -> None:
    async with uow_factory() as uow:
        await uow.users.add(user)
        await uow.commit()
