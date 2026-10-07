"""The search index follows the documents: events lead to jobs, jobs write the index."""

from collections.abc import Sequence
from datetime import date

import pytest

from papiq.adapters.outbound.memory import (
    FakeEmbeddings,
    MemorySearchIndex,
)
from papiq.core.domain.attributes import AttributeType
from papiq.core.domain.documents import DocumentChanges
from papiq.core.domain.errors import EmbeddingsError, PermissionDeniedError, SearchIndexError
from papiq.core.domain.jobs import JobStatus
from papiq.core.domain.pipeline import Lane, Step
from papiq.core.domain.search import IndexDocument
from papiq.core.domain.users import Role
from papiq.core.services.indexing import (
    INDEX_JOB,
    RECONCILE_JOB,
    REFRESH_JOB,
    IndexingPolicy,
    split_sections,
)
from tests.builders import UNCERTAIN, incoming, index_document
from tests.unit.services.conftest import Returns, World
from tests.unit.services.search_support import (
    HOUR,
    BrokenEmbeddings,
    HookedIndex,
    RacingIndex,
    Setup,
    index_jobs,
    run_due,
)


@pytest.fixture
def setup(world: World) -> Setup:
    return Setup(world)


async def test_a_document_in_processing_is_indexed_for_its_owner(setup: Setup) -> None:
    owner = await setup.world.user()
    received = await setup.world.pipeline().receive(
        owner.id, incoming(b"%PDF-1 a"), filename="a.pdf"
    )
    await setup.put_text(received.id, "Stromrechnung")
    await setup.settle(pipeline=False)
    entry = setup.entry(received.id)
    assert entry.lane_value == "processing"
    assert entry.owner_id == owner.id
    assert entry.text == "Stromrechnung"
    assert entry.filename == "a.pdf"


async def test_the_lane_follows_the_pipeline(setup: Setup) -> None:
    owner = await setup.world.user()
    document = await setup.document(owner)
    assert (setup.entry(document.id).lane_value, document.lane) == ("green", Lane.GREEN)

    await setup.world.pipeline().reprocess_from(owner.id, document.id, Step.CLASSIFY)
    await setup.settle(pipeline_service=setup.world.pipeline({Step.CLASSIFY: Returns(UNCERTAIN)}))
    assert setup.entry(document.id).lane_value == "yellow"


async def test_metadata_reaches_the_index_with_names_and_ids(setup: Setup) -> None:
    admin, owner = await setup.world.user(role=Role.ADMIN), await setup.world.user()
    contact = await setup.master_data.create_contact(admin.id, "Stadtwerke")
    kind = await setup.master_data.create_document_type(admin.id, "Rechnung")
    tag = await setup.master_data.create_tag(admin.id, "Energie")
    meter = await setup.master_data.create_attribute(admin.id, "Zähler", AttributeType.TEXT)
    document = await setup.document(owner)
    await setup.world.documents.update_metadata(
        owner.id,
        document.id,
        DocumentChanges(
            title="Strom 2026",
            contact_id=contact.id,
            document_type_id=kind.id,
            tag_ids=frozenset({tag.id}),
            document_date=date(2026, 9, 30),
            attributes={meter.id: "4711"},
        ),
    )
    await setup.settle()
    entry = setup.entry(document.id)
    assert (entry.title, entry.contact, entry.document_type) == (
        "Strom 2026",
        "Stadtwerke",
        "Rechnung",
    )
    assert (entry.contact_id, entry.document_type_id) == (contact.id, kind.id)
    assert (entry.tag_ids, entry.tags) == ((tag.id,), ("Energie",))
    assert entry.attributes == ("4711",)
    assert entry.document_date == date(2026, 9, 30)
    assert entry.version == (await setup.world.documents.get(owner.id, document.id)).version


async def test_a_deleted_document_leaves_the_index(setup: Setup) -> None:
    owner = await setup.world.user()
    document = await setup.document(owner)
    await setup.world.documents.delete(owner.id, document.id)
    await setup.settle()
    assert document.id not in setup.index.documents


async def test_a_move_changes_the_drawer(setup: Setup) -> None:
    owner = await setup.world.user()
    document = await setup.document(owner)
    target = await setup.world.drawers.create(owner.id, "Archive")
    await setup.world.documents.move(owner.id, document.id, target.id)
    await setup.settle()
    assert setup.entry(document.id).drawer_id == target.id


async def test_renaming_master_data_updates_the_documents_that_carry_it(setup: Setup) -> None:
    admin, owner = await setup.world.user(role=Role.ADMIN), await setup.world.user()
    contact = await setup.master_data.create_contact(admin.id, "Stadtwerke")
    tag = await setup.master_data.create_tag(admin.id, "Energie")
    with_contact = await setup.document(owner, "a")
    with_tag = await setup.document(owner, "b")
    neither = await setup.document(owner, "c")
    for id, changes in (
        (with_contact.id, DocumentChanges(contact_id=contact.id)),
        (with_tag.id, DocumentChanges(tag_ids=frozenset({tag.id}))),
    ):
        await setup.world.documents.update_metadata(owner.id, id, changes)
    await setup.settle()

    await setup.master_data.rename_contact(admin.id, contact.id, "Energie AG")
    await setup.master_data.rename_tag(admin.id, tag.id, "Strom")
    assert sorted(
        job.kind for job in setup.world.database.jobs.values() if job.status.is_active
    ) == [
        REFRESH_JOB,
        REFRESH_JOB,
    ]
    await setup.settle()
    assert setup.entry(with_contact.id).contact == "Energie AG"
    assert setup.entry(with_tag.id).tags == ("Strom",)
    assert setup.entry(neither.id).version == neither.version


async def test_renaming_queues_nothing_without_a_search_index(world: World) -> None:
    admin = await world.user(role=Role.ADMIN)
    contact = await world.master_data.create_contact(admin.id, "Stadtwerke")
    await world.master_data.rename_contact(admin.id, contact.id, "Energie AG")
    assert not world.database.jobs


async def test_a_change_without_new_text_keeps_the_vectors(setup: Setup) -> None:
    owner = await setup.world.user()
    document = await setup.document(owner)
    assert isinstance(setup.embeddings, FakeEmbeddings)
    calls = len(setup.embeddings.calls)
    stamp = (await setup.index.state(document.id)).embedding  # type: ignore[union-attr]
    assert stamp is not None

    target = await setup.world.drawers.create(owner.id, "Archive")
    await setup.world.documents.move(owner.id, document.id, target.id)
    await setup.settle()
    assert len(setup.embeddings.calls) == calls
    assert (await setup.index.state(document.id)).embedding == stamp  # type: ignore[union-attr]

    await setup.world.documents.update_metadata(
        owner.id, document.id, DocumentChanges(title="Anderer Titel")
    )
    await setup.settle()
    assert len(setup.embeddings.calls) == calls + 1  # the title is part of the first section
    assert (await setup.index.state(document.id)).embedding != stamp  # type: ignore[union-attr]


async def test_without_embeddings_the_index_has_no_vectors(world: World) -> None:
    setup = Setup(world, embeddings=None)
    document = await setup.document(await world.user())
    assert setup.entry(document.id).vectors == ()
    assert setup.entry(document.id).embedding is None


async def test_a_long_text_gets_one_vector_per_section(world: World) -> None:
    setup = Setup(world, policy=IndexingPolicy(chunk_size=20, max_chunks=3, max_text=70))
    text = "\n\n".join(f"paragraph number {n}" for n in range(10))
    document = await setup.document(await world.user(), text)
    entry = setup.entry(document.id)
    assert len(entry.vectors or ()) == 3
    assert len(entry.text) == 70  # cut at the limit


async def test_a_document_without_text_is_indexed_by_what_it_says_about_itself(
    setup: Setup,
) -> None:
    owner = await setup.world.user()
    received = await setup.world.pipeline().receive(
        owner.id, incoming(b"%PDF-1 b"), filename="b.pdf"
    )
    await setup.settle(pipeline=False)  # no text stored yet
    entry = setup.entry(received.id)
    assert entry.text == ""
    assert len(entry.vectors or ()) == 1


# --- failures ------------------------------------------------------------------------------------


async def test_a_failing_embedding_service_is_waited_for(world: World) -> None:
    setup = Setup(world, embeddings=BrokenEmbeddings())
    owner = await world.user()
    received = await world.pipeline().receive(owner.id, incoming(b"%PDF-1 c"), filename="c.pdf")
    await setup.put_text(received.id, "Text")
    await setup.bus.dispatch()
    indexing = setup.indexing
    assert await run_due(indexing) == 2  # the events `received` and `step_completed`
    assert setup.entry(received.id).vectors == ()  # found by its words, the vectors follow
    jobs = index_jobs(world)
    assert [job.status for job in jobs] == [JobStatus.QUEUED] * 2
    assert all(job.run_at > world.clock.now() for job in jobs)
    assert all("connection refused" in (job.last_error or "") for job in jobs)
    assert not await indexing.run_next_job()  # not due yet

    assert isinstance(setup.embeddings, BrokenEmbeddings)
    setup.embeddings.up = True
    world.clock.advance(HOUR)
    assert await run_due(indexing) == 2
    assert len(setup.entry(received.id).vectors or ()) == 1


async def test_the_last_attempt_writes_without_vectors_and_the_reconciliation_adds_them(
    world: World,
) -> None:
    broken = BrokenEmbeddings()
    setup = Setup(world, embeddings=broken)
    owner = await world.user()
    received = await world.pipeline().receive(owner.id, incoming(b"%PDF-1 d"), filename="d.pdf")
    await setup.put_text(received.id, "Text")
    await setup.bus.dispatch()
    indexing = setup.indexing
    for _ in range(10):
        world.clock.advance(HOUR)
        assert await run_due(indexing) == 2
    entry = setup.entry(received.id)
    assert (entry.vectors, entry.embedding) == ((), None)
    assert {job.status for job in index_jobs(world)} == {JobStatus.DONE}

    broken.up = True
    assert (await indexing.reconcile()).queued == 1
    await setup.settle(pipeline=False)
    assert len(setup.entry(received.id).vectors or ()) == 1


async def test_an_embedding_failure_keeps_the_vectors_that_are_there(world: World) -> None:
    broken = BrokenEmbeddings()
    broken.up = True
    setup = Setup(world, embeddings=broken)
    owner = await world.user()
    document = await setup.document(owner)
    before = setup.entry(document.id)
    assert before.embedding is not None
    stamp = before.embedding

    broken.up = False
    await world.documents.update_metadata(owner.id, document.id, DocumentChanges(title="Neu"))
    await setup.settle(pipeline=False)
    entry = setup.entry(document.id)
    assert entry.title == "Neu"  # the words follow at once
    assert entry.vectors == before.vectors  # the vectors of the old text stay
    assert entry.embedding == stamp

    broken.up = True
    world.clock.advance(HOUR)
    await setup.settle(pipeline=False)
    assert setup.entry(document.id).embedding != stamp


async def test_vectors_of_another_length_are_refused(world: World) -> None:
    setup = Setup(world, policy=IndexingPolicy(dimensions=3))  # the fake vectors have more
    owner = await world.user()
    received = await world.pipeline().receive(owner.id, incoming(b"%PDF-1 f"), filename="f.pdf")
    await setup.put_text(received.id, "Text")
    with pytest.raises(EmbeddingsError, match="PAPIQ_EMBEDDING_DIMENSIONS"):
        await setup.indexing.index_document(received.id)
    assert setup.entry(received.id).vectors == ()  # still found by its words
    await setup.indexing.index_document(received.id, tolerate_embedding_failure=True)
    assert setup.entry(received.id).embedding is None


async def test_a_job_that_keeps_failing_is_given_up(world: World) -> None:
    class Failing(MemorySearchIndex):
        async def upsert(self, documents: Sequence[IndexDocument]) -> None:
            raise SearchIndexError("rejected")

    setup = Setup(world, index=Failing(), embeddings=None)
    owner = await world.user()
    await world.pipeline().receive(owner.id, incoming(b"%PDF-1 e"), filename="e.pdf")
    await setup.bus.dispatch()
    indexing = setup.indexing
    for _ in range(10):
        world.clock.advance(HOUR * 2)
        await run_due(indexing)
    jobs = index_jobs(world)
    assert {job.status for job in jobs} == {JobStatus.FAILED}
    assert all("rejected" in (job.last_error or "") for job in jobs)


async def test_a_job_with_a_broken_payload_fails(setup: Setup) -> None:
    async with setup.world.uow() as uow:
        await uow.jobs.enqueue(
            INDEX_JOB, {"document_id": "nonsense"}, run_at=setup.world.clock.now()
        )
        await uow.commit()
    assert await setup.indexing.run_next_job()
    (job,) = setup.world.database.jobs.values()
    assert job.status is JobStatus.FAILED
    assert "invalid payload" in (job.last_error or "")


async def test_two_jobs_for_one_document_leave_the_newest_state_in_the_index(world: World) -> None:
    index = RacingIndex()
    setup = Setup(world, index=index, embeddings=None)
    owner = await world.user()
    document = await setup.document(owner)

    async def newer_job() -> None:
        # While the first job is about to write the old state, the title changes and a second
        # job writes the new state: the first one then overwrites it with the old one.
        await world.documents.update_metadata(owner.id, document.id, DocumentChanges(title="Neu"))
        await setup.indexing.index_document(document.id)

    await world.documents.update_metadata(owner.id, document.id, DocumentChanges(title="Alt"))
    index.before_write = newer_job
    await setup.indexing.index_document(document.id)
    assert setup.entry(document.id).title == "Neu"


async def test_a_document_deleted_while_it_is_indexed_leaves_no_entry(world: World) -> None:
    index = RacingIndex()
    setup = Setup(world, index=index, embeddings=None)
    owner = await world.user()
    document = await setup.document(owner)

    index.before_write = lambda: world.documents.delete(owner.id, document.id)
    await setup.indexing.index_document(document.id)
    assert document.id not in index.documents


# --- reconciliation and rebuild ------------------------------------------------------------------


async def test_the_reconciliation_queues_what_is_missing_or_stale_and_removes_orphans(
    setup: Setup,
) -> None:
    owner = await setup.world.user()
    current = await setup.document(owner, "current")
    stale = await setup.document(owner, "stale")
    missing = await setup.world.pipeline().receive(
        owner.id, incoming(b"%PDF-1 f"), filename="f.pdf"
    )
    await setup.index.upsert([index_document(id=stale.id, version=stale.version - 1)])
    orphan = index_document()
    await setup.index.upsert([orphan])

    result = await setup.indexing.reconcile()
    assert (result.queued, result.removed) == (2, 1)
    assert orphan.id not in setup.index.documents
    await setup.settle(pipeline=False)
    assert setup.entry(missing.id).lane_value == "processing"
    assert setup.entry(stale.id).version == stale.version
    assert setup.entry(current.id).version == current.version


async def test_the_reconciliation_notices_vectors_of_another_model(world: World) -> None:
    owner = await world.user()
    first = Setup(world, embeddings=FakeEmbeddings(model="old"))
    document = await first.document(owner)
    second = Setup(world, index=first.index, embeddings=FakeEmbeddings(model="new"))
    assert (await second.indexing.reconcile()).queued == 1
    await second.settle(pipeline=False)
    assert (await first.index.state(document.id)).embedding.model == "new"  # type: ignore[union-attr]
    assert (await second.indexing.reconcile()).queued == 0


async def test_the_reconciliation_repeats_itself(setup: Setup) -> None:
    await setup.indexing.schedule()
    await setup.indexing.schedule()
    jobs = setup.world.database.jobs.values()
    # One that recurs, and one for each start of a worker, which runs once.
    assert sorted(j.payload.get("once") is True for j in jobs) == [False, True, True]
    assert all(j.kind == RECONCILE_JOB for j in jobs)
    while await setup.indexing.run_next_job():
        pass
    queued = [j for j in setup.world.database.jobs.values() if j.status is JobStatus.QUEUED]
    assert [j.kind for j in queued] == [RECONCILE_JOB]
    assert queued[0].run_at == setup.world.clock.now() + setup.policy.reconcile_interval


async def test_a_rebuild_replaces_the_index_from_database_and_store(setup: Setup) -> None:
    owner = await setup.world.user()
    one, two = await setup.document(owner, "one"), await setup.document(owner, "two")
    orphan = index_document()
    setup.index.replace_all({orphan.id: orphan})
    progress: list[tuple[int, int]] = []

    result = await setup.indexing.rebuild(lambda done, total: progress.append((done, total)))
    assert (result.documents, result.queued, result.removed) == (2, 0, 0)
    assert set(setup.index.documents) == {one.id, two.id}
    assert progress[-1] == (2, 2)
    assert len(setup.entry(one.id).vectors or ()) == 1


async def test_a_rebuild_notices_changes_made_while_it_ran(world: World) -> None:
    index = HookedIndex()
    setup = Setup(world, index=index, policy=IndexingPolicy(batch_size=1))
    owner = await world.user()
    first, second = await setup.document(owner, "one"), await setup.document(owner, "two")
    earlier = min(first.id, second.id)  # the build takes the documents in the order of the ids

    async def change() -> None:
        await world.documents.update_metadata(owner.id, earlier, DocumentChanges(title="Geändert"))

    index.after_first_batch = change
    result = await setup.indexing.rebuild()
    assert (result.documents, result.queued) == (2, 1)  # the new index has the old version
    await setup.settle(pipeline=False)
    assert setup.entry(earlier).title == "Geändert"


async def test_a_rebuild_notices_a_rename_made_while_it_ran(world: World) -> None:
    index = HookedIndex()
    setup = Setup(world, index=index, policy=IndexingPolicy(batch_size=1))
    owner, admin = await world.user(), await world.user(role=Role.ADMIN)
    first, second = await setup.document(owner, "one"), await setup.document(owner, "two")
    contact = await setup.master_data.create_contact(admin.id, "Stadtwerke")
    for document in (first, second):
        changes = DocumentChanges(contact_id=contact.id)
        await world.documents.update_metadata(owner.id, document.id, changes)
    await setup.settle(pipeline=False)

    async def rename() -> None:
        # The refresh and index jobs run now and write to the index that is about to be replaced.
        await setup.master_data.rename_contact(admin.id, contact.id, "Energie AG")
        await setup.settle(pipeline=False)

    index.after_first_batch = rename
    result = await setup.indexing.rebuild()
    assert result.queued >= 1  # a document built with the old name; the version did not change
    await setup.settle(pipeline=False)
    assert {setup.entry(first.id).contact, setup.entry(second.id).contact} == {"Energie AG"}


async def test_only_admins_request_a_rebuild(setup: Setup) -> None:
    admin, user = await setup.world.user(role=Role.ADMIN), await setup.world.user()
    with pytest.raises(PermissionDeniedError):
        await setup.indexing.request_rebuild(user.id)
    document = await setup.document(user)
    await setup.indexing.request_rebuild(admin.id)
    await setup.indexing.request_rebuild(admin.id)  # one is queued already
    setup.index.replace_all({})
    assert await setup.indexing.run_next_job()
    assert not await setup.indexing.run_next_job()
    assert set(setup.index.documents) == {document.id}


# --- sections ------------------------------------------------------------------------------------


def test_short_text_is_one_section() -> None:
    assert split_sections("Hello world", 100, 8) == ["Hello world"]


def test_no_text_is_no_section() -> None:
    assert split_sections("", 100, 8) == []
    assert split_sections("\n\n  \n\n", 100, 8) == []


def test_sections_are_cut_at_paragraph_breaks() -> None:
    text = "aaaa bbbb\n\ncccc dddd\n\neeee ffff"
    assert split_sections(text, 20, 8) == ["aaaa bbbb\n\ncccc dddd", "eeee ffff"]


def test_a_paragraph_longer_than_a_section_is_cut_at_a_space() -> None:
    sections = split_sections("one two three four five six", 12, 8)
    assert all(len(section) <= 12 for section in sections)
    assert " ".join(sections) == "one two three four five six"


def test_a_word_longer_than_a_section_is_cut_hard() -> None:
    assert split_sections("x" * 25, 10, 8) == ["x" * 10, "x" * 10, "x" * 5]


def test_the_number_of_sections_is_limited() -> None:
    text = "\n\n".join(["w" * 10] * 20)
    assert len(split_sections(text, 10, 3)) == 3
    assert len(split_sections(text, 10, 1)) == 1


def test_limits_must_be_positive() -> None:
    with pytest.raises(ValueError):
        IndexingPolicy(max_chunks=0)
