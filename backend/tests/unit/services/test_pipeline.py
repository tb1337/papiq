import asyncio
from datetime import timedelta
from uuid import UUID

import pytest

from papiq.adapters.outbound.memory import MemoryObjectStore
from papiq.core.domain.documents import Document, DocumentChanges, Sha256
from papiq.core.domain.drawers import ShareLevel
from papiq.core.domain.errors import (
    DuplicateDocumentError,
    InvalidTransitionError,
    NotFoundError,
    PermissionDeniedError,
    UnprocessableDocumentError,
    UnsupportedMediaTypeError,
)
from papiq.core.domain.events import (
    DocumentFiled,
    DocumentReceived,
    LaneChanged,
    StepCompleted,
)
from papiq.core.domain.ids import ContactId, TagId
from papiq.core.domain.jobs import JobStatus
from papiq.core.domain.master_data import Tag
from papiq.core.domain.pipeline import (
    PIPELINE,
    Lane,
    Outcome,
    ProcessingStatus,
    Step,
    StepResult,
)
from papiq.core.domain.users import User
from papiq.core.services.objects import original_key
from papiq.core.services.pipeline import (
    STEP_JOB,
    MetadataResult,
    PipelineService,
    PlaceholderStep,
    RetryPolicy,
)
from tests.builders import FAILED, NOW, UNCERTAIN, incoming
from tests.contracts.processing import SAMPLES
from tests.unit.services.conftest import Raises, Returns, World

PDF = b"%PDF-1.7 invoice"


async def receive(world: World, pipeline: PipelineService | None = None) -> tuple[User, Document]:
    owner = await world.user()
    document = await (pipeline or world.pipeline()).receive(
        owner.id, incoming(PDF), filename="invoice.pdf"
    )
    return owner, document


async def test_receive_stores_original_document_events_and_job(world: World) -> None:
    owner = await world.user()
    document = await world.pipeline().receive(owner.id, incoming(PDF), filename="invoice.pdf")
    assert document.sha256 == Sha256.of(PDF)
    assert await world.object_store.get(original_key(document.sha256)) == PDF
    assert document.drawer_id == (await world.default_drawer(owner)).id
    assert document.processing.current_step is Step.OCR
    assert document.lane is None

    assert world.event_types() == ["document.received", "document.step_completed"]
    async with world.uow() as uow:
        assert await uow.documents.get(document.id) == document
        (entry,) = await uow.processing_log.list_for(document.id)
        assert (entry.step, entry.run, entry.result.outcome) == (Step.RECEIVE, 1, Outcome.OK)
        assert entry.result.output == {
            "sha256": document.sha256.hex,
            "size": len(PDF),
            "media_type": "application/pdf",
        }
        assert entry.pipeline_version == "test"
        job = await uow.jobs.claim(now=NOW, lease=timedelta(minutes=1))
    assert job is not None and job.kind == STEP_JOB
    assert job.payload == {"document_id": str(document.id), "step": "ocr", "run": 1}


async def test_pipeline_runs_to_green(world: World) -> None:
    owner = await world.user()
    document = await world.pipeline().receive(owner.id, incoming(PDF), filename="invoice.pdf")
    assert await world.drain() == len(PIPELINE) - 1
    stored = await world.documents.get(owner.id, document.id)
    assert stored.processing.status is ProcessingStatus.COMPLETED
    assert stored.lane is Lane.GREEN

    events = world.events()
    assert isinstance(events[0], DocumentReceived)
    steps = [event.step for event in events if isinstance(event, StepCompleted)]
    assert steps == list(PIPELINE)
    assert isinstance(events[-2], DocumentFiled)
    assert isinstance(events[-1], LaneChanged) and events[-1].new is Lane.GREEN
    log = await world.documents.processing_log(owner.id, document.id)
    assert [entry.step for entry in log] == list(PIPELINE)
    assert len(world.database.jobs) == len(PIPELINE) - 1
    assert all(job.status is JobStatus.DONE for job in world.database.jobs.values())


async def test_uncertain_step_ends_yellow(world: World) -> None:
    pipeline = world.pipeline({Step.CLASSIFY: Returns(UNCERTAIN)})
    owner, document = await receive(world, pipeline)
    await world.drain(pipeline)
    stored = await world.documents.get(owner.id, document.id)
    assert stored.lane is Lane.YELLOW
    log = await world.documents.processing_log(owner.id, stored.id)
    classify = next(entry for entry in log if entry.step is Step.CLASSIFY)
    assert classify.result.reason == "new contact"


async def test_failed_result_stops_red(world: World) -> None:
    pipeline = world.pipeline({Step.PARSE: Returns(FAILED)})
    owner, document = await receive(world, pipeline)
    assert await world.drain(pipeline) == 2
    stored = await world.documents.get(owner.id, document.id)
    assert stored.lane is Lane.RED
    assert stored.processing.status is ProcessingStatus.FAILED
    assert stored.processing.current_step is Step.PARSE


async def test_exceptions_are_retried_with_backoff(world: World) -> None:
    flaky = Raises(times=2)
    pipeline = world.pipeline({Step.OCR: flaky})
    owner, document = await receive(world, pipeline)
    assert await pipeline.run_next_job()  # attempt 1 fails
    assert not await pipeline.run_next_job()  # waits 30 s
    world.clock.advance(timedelta(seconds=30))
    assert await pipeline.run_next_job()  # attempt 2 fails
    world.clock.advance(timedelta(seconds=59))
    assert not await pipeline.run_next_job()  # waits 60 s
    world.clock.advance(timedelta(seconds=1))
    await world.drain(pipeline)  # attempt 3 succeeds, the rest follows
    assert flaky.calls == 3
    stored = await world.documents.get(owner.id, document.id)
    assert stored.lane is Lane.GREEN
    ocr_job = next(job for job in world.database.jobs.values() if job.payload["step"] == "ocr")
    assert (ocr_job.attempts, ocr_job.status) == (3, JobStatus.DONE)
    assert ocr_job.last_error == "OSError: scanner glitch"


async def test_exhausted_retries_end_red_and_retry_recovers(world: World) -> None:
    broken = Raises(times=3)
    pipeline = world.pipeline({Step.OCR: broken})
    owner, document = await receive(world, pipeline)
    for _ in range(3):
        assert await pipeline.run_next_job()
        world.clock.advance(timedelta(minutes=5))
    assert not await pipeline.run_next_job()
    stored = await world.documents.get(owner.id, document.id)
    assert stored.lane is Lane.RED
    log = await world.documents.processing_log(owner.id, stored.id)
    assert log[-1].result.outcome is Outcome.FAILED
    assert log[-1].result.reason == "OSError: scanner glitch"
    ocr_job = next(job for job in world.database.jobs.values() if job.payload["step"] == "ocr")
    assert ocr_job.status is JobStatus.FAILED

    retried = await pipeline.retry(owner.id, stored.id)
    assert retried.processing.run == 2
    assert retried.lane is None
    await world.drain(pipeline)
    stored = await world.documents.get(owner.id, stored.id)
    assert stored.lane is Lane.GREEN
    assert broken.calls == 4


async def test_reprocess_from_a_step(world: World) -> None:
    owner, document = await receive(world)
    await world.drain()
    world.database.outbox.clear()
    pipeline = world.pipeline({Step.EXTRACT_ATTRIBUTES: Returns(UNCERTAIN)})
    await pipeline.reprocess_from(owner.id, document.id, Step.CLASSIFY)
    assert await world.drain(pipeline) == 3  # waits before filing
    stored = await world.documents.get(owner.id, document.id)
    assert stored.lane is Lane.YELLOW
    assert stored.processing.status is ProcessingStatus.REVIEW
    assert stored.processing.run == 2
    lanes = [(e.old, e.new) for e in world.events() if isinstance(e, LaneChanged)]
    assert lanes == [(Lane.GREEN, None), (None, Lane.YELLOW)]


async def test_retry_and_reprocess_are_for_the_owner(world: World) -> None:
    owner = await world.user()
    writer = await world.user()
    shared = await world.drawers.create(owner.id, "Shared")
    await world.drawers.share(owner.id, shared.id, writer.id, ShareLevel.READ_WRITE)
    document = await world.pipeline().receive(
        owner.id, incoming(PDF), filename="a.pdf", drawer=shared.id
    )
    await world.drain()
    with pytest.raises(PermissionDeniedError):
        await world.pipeline().reprocess_from(writer.id, document.id, Step.OCR)
    stranger = await world.user()
    with pytest.raises(NotFoundError):
        await world.pipeline().reprocess_from(stranger.id, document.id, Step.OCR)
    with pytest.raises(InvalidTransitionError):
        await world.pipeline().retry(owner.id, document.id)


async def test_duplicates_are_rejected_per_owner(world: World) -> None:
    owner, document = await receive(world)
    with pytest.raises(DuplicateDocumentError) as info:
        await world.pipeline().receive(owner.id, incoming(PDF), filename="again.pdf")
    assert info.value.existing == document.id
    other = await world.user()
    copy = await world.pipeline().receive(other.id, incoming(PDF), filename="mine.pdf")
    assert copy.sha256 == document.sha256
    assert copy.owner_id == other.id


async def test_receive_into_a_drawer_needs_write_access(world: World) -> None:
    owner, writer, reader = await world.user(), await world.user(), await world.user()
    shared = await world.drawers.create(owner.id, "Shared")
    await world.drawers.share(owner.id, shared.id, writer.id, ShareLevel.READ_WRITE)
    await world.drawers.share(owner.id, shared.id, reader.id, ShareLevel.READ)
    document = await world.pipeline().receive(
        writer.id, incoming(PDF), filename="a.pdf", drawer=shared.id
    )
    assert (document.owner_id, document.drawer_id) == (writer.id, shared.id)
    with pytest.raises(PermissionDeniedError):
        await world.pipeline().receive(
            reader.id, incoming(b"%PDF-1.7 other"), filename="b.pdf", drawer=shared.id
        )
    with pytest.raises(NotFoundError):
        await world.pipeline().receive(
            reader.id,
            incoming(b"%PDF-1.7 other"),
            filename="b.pdf",
            drawer=(await world.default_drawer(owner)).id,
        )


async def test_stale_jobs_are_skipped(world: World) -> None:
    owner, document = await receive(world)
    await world.documents.delete(owner.id, document.id)
    calls = Returns(FAILED)
    pipeline = world.pipeline({Step.OCR: calls})
    assert await pipeline.run_next_job()
    assert calls.calls == 0
    assert not await pipeline.run_next_job()
    steps = [job for job in world.database.jobs.values() if job.kind == STEP_JOB]
    assert all(job.status is JobStatus.DONE for job in steps)


async def crash(world: World) -> None:
    """A worker claims the next job and dies; its lease runs out."""
    async with world.uow() as uow:
        assert await uow.jobs.claim(now=world.clock.now(), lease=timedelta(minutes=10))
        await uow.commit()
    world.clock.advance(timedelta(minutes=10))


async def test_a_step_that_keeps_killing_the_worker_ends_red(world: World) -> None:
    owner, document = await receive(world)
    for _ in range(3):
        await crash(world)
    ocr = Returns(FAILED)
    pipeline = world.pipeline({Step.OCR: ocr})
    assert await pipeline.run_next_job()
    assert ocr.calls == 0
    stored = await world.documents.get(owner.id, document.id)
    assert stored.lane is Lane.RED
    log = await world.documents.processing_log(owner.id, document.id)
    assert log[-1].result.reason is not None and "ran out of time" in log[-1].result.reason
    assert not await pipeline.run_next_job()


async def test_a_worker_that_lost_its_claim_stores_nothing(world: World) -> None:
    owner, document = await receive(world)

    class Slow:
        """Runs longer than the lease; meanwhile another worker takes the job over."""

        async def run(self, document: Document) -> StepResult:
            world.clock.advance(timedelta(minutes=11))
            async with world.uow() as uow:
                assert await uow.jobs.claim(now=world.clock.now(), lease=timedelta(minutes=10))
                await uow.commit()
            return FAILED

    assert await world.pipeline({Step.OCR: Slow()}).run_next_job()
    stored = await world.documents.get(owner.id, document.id)
    assert stored.processing.current_step is Step.OCR
    assert stored.lane is None
    (job,) = [job for job in world.database.jobs.values() if job.payload["step"] == "ocr"]
    assert (job.status, job.attempts) == (JobStatus.RUNNING, 2)


async def test_invalid_job_payload_fails_the_job(world: World) -> None:
    async with world.uow() as uow:
        await uow.jobs.enqueue(STEP_JOB, {"step": "ocr"}, run_at=world.clock.now())
        await uow.commit()
    assert await world.pipeline().run_next_job()
    (job,) = world.database.jobs.values()
    assert job.status is JobStatus.FAILED
    assert job.last_error is not None and job.last_error.startswith("invalid payload")


async def test_every_step_needs_an_executor(world: World) -> None:
    with pytest.raises(ValueError, match="no executor"):
        PipelineService(
            world.uow,
            world.clock,
            world.object_store,
            {Step.OCR: PlaceholderStep()},
            pipeline_version="test",
        )


def test_retry_policy() -> None:
    policy = RetryPolicy(max_attempts=3, delay=timedelta(seconds=10))
    assert policy.next_run(1, NOW) == NOW + timedelta(seconds=10)
    assert policy.next_run(2, NOW) == NOW + timedelta(seconds=20)
    assert policy.next_run(3, NOW) is None


class Blocks:
    """Step executor that waits until cancelled."""

    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def run(self, document: Document) -> StepResult:
        self.started.set()
        await asyncio.Event().wait()
        raise AssertionError("not reached")


class Unprocessable:
    def __init__(self) -> None:
        self.calls = 0

    async def run(self, document: Document) -> StepResult:
        self.calls += 1
        raise UnprocessableDocumentError("the PDF is encrypted")


async def test_an_unprocessable_document_fails_at_once(world: World) -> None:
    ocr = Unprocessable()
    pipeline = world.pipeline({Step.OCR: ocr})
    owner, document = await receive(world, pipeline)
    await world.drain(pipeline)
    stored = await world.documents.get(owner.id, document.id)
    assert stored.lane is Lane.RED
    assert ocr.calls == 1  # no automatic retry
    log = await world.documents.processing_log(owner.id, document.id)
    assert (log[-1].step, log[-1].result.reason) == (Step.OCR, "the PDF is encrypted")
    (job,) = [job for job in world.database.jobs.values() if job.payload["step"] == "ocr"]
    assert job.status is JobStatus.FAILED


async def test_a_cancelled_step_releases_its_job_at_once(world: World) -> None:
    """Interruptions (the worker stops) do not use up the attempts of the retry policy."""
    blocking = Blocks()
    pipeline = world.pipeline({Step.OCR: blocking})
    owner, document = await receive(world, pipeline)
    for _ in range(5):  # more than RetryPolicy.max_attempts
        blocking.started.clear()
        task = asyncio.create_task(pipeline.run_next_job())
        await blocking.started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    (job,) = [job for job in world.database.jobs.values() if job.payload["step"] == "ocr"]
    assert (job.status, job.tries, job.run_at, job.last_error) == (
        JobStatus.QUEUED,
        0,
        world.clock.now(),
        "interrupted: the worker stopped",
    )
    await world.drain()  # another worker takes it over without waiting for the lease
    assert (await world.documents.get(owner.id, document.id)).lane is Lane.GREEN


@pytest.mark.parametrize(
    ("sample", "media_type"),
    [("scan.pdf", "application/pdf"), ("photo.jpg", "image/jpeg")],
)
async def test_the_media_type_comes_from_the_content(
    world: World, sample: str, media_type: str
) -> None:
    owner = await world.user()
    document = await world.pipeline().receive(
        owner.id, incoming((SAMPLES / sample).read_bytes()), filename="named-wrongly.txt"
    )
    assert document.media_type == media_type
    assert world.object_store.content_type(original_key(document.sha256)) == media_type


async def test_an_unsupported_file_is_rejected_and_not_stored(world: World) -> None:
    owner = await world.user()
    file = incoming((SAMPLES / "unsupported.docx").read_bytes())
    with pytest.raises(UnsupportedMediaTypeError, match="unsupported file type"):
        await world.pipeline().receive(owner.id, file, filename="letter.pdf")
    assert not await world.object_store.exists(original_key(file.sha256))
    assert world.events() == []


async def test_a_stored_original_is_not_uploaded_again(world: World) -> None:
    first, second = await world.user(), await world.user()
    document = await world.pipeline().receive(first.id, incoming(PDF), filename="a.pdf")
    key = original_key(document.sha256)
    await world.object_store.put(key, b"%PDF-1.7 marker", content_type="application/pdf")
    await world.pipeline().receive(second.id, incoming(PDF), filename="b.pdf")
    assert await world.object_store.get(key) == b"%PDF-1.7 marker"


class VanishingStore(MemoryObjectStore):
    """Says an original exists at the first look, then it is gone: as if the files of a deleted
    document were removed right between the two looks of an upload."""

    def __init__(self) -> None:
        super().__init__()
        self.looks = 0

    async def exists(self, key: str) -> bool:
        self.looks += 1
        return self.looks == 1 or await super().exists(key)


async def test_an_upload_stores_the_original_again_if_it_vanished(world: World) -> None:
    world.object_store = VanishingStore()
    owner = await world.user()
    document = await world.pipeline().receive(owner.id, incoming(b"%PDF-1.7 x"), filename="x.pdf")
    assert await world.object_store.get(original_key(document.sha256)) == b"%PDF-1.7 x"


class Changes:
    """Classifies with a metadata change."""

    def __init__(self, changes: DocumentChanges, add_tags: frozenset[TagId] = frozenset()) -> None:
        self.changes = changes
        self.add_tags = add_tags

    async def run(self, document: Document) -> MetadataResult:
        return MetadataResult(StepResult(outcome=Outcome.OK), self.changes, self.add_tags)


async def test_a_step_changes_metadata_with_its_result(world: World) -> None:
    tag = Tag.create(name="Strom", now=NOW)
    async with world.uow() as uow:
        await uow.tags.add(tag)
        await uow.commit()
    changes = DocumentChanges(tag_ids=frozenset({tag.id}))
    pipeline = world.pipeline({Step.CLASSIFY: Changes(changes)})
    owner, document = await receive(world, pipeline)
    await world.drain(pipeline)
    stored = await world.documents.get(owner.id, document.id)
    assert stored.tag_ids == frozenset({tag.id})
    assert stored.lane is Lane.GREEN


async def test_classified_tags_are_added_to_the_documents_tags(world: World) -> None:
    mine, strom = Tag.create(name="Mine", now=NOW), Tag.create(name="Strom", now=NOW)
    async with world.uow() as uow:
        await uow.tags.add(mine)
        await uow.tags.add(strom)
        await uow.commit()
    pipeline = world.pipeline({Step.CLASSIFY: Changes(DocumentChanges(), frozenset({strom.id}))})
    owner, document = await receive(world, pipeline)
    await world.documents.update_metadata(
        owner.id, document.id, DocumentChanges(tag_ids=frozenset({mine.id}))
    )
    await world.drain(pipeline)
    stored = await world.documents.get(owner.id, document.id)
    assert stored.tag_ids == frozenset({mine.id, strom.id})
    assert stored.lane is Lane.GREEN


async def test_changes_that_no_longer_fit_make_the_step_uncertain(world: World) -> None:
    changes = DocumentChanges(
        contact_id=ContactId(UUID(int=1)), tag_ids=frozenset({TagId(UUID(int=2))})
    )
    pipeline = world.pipeline({Step.CLASSIFY: Changes(changes)})
    owner, document = await receive(world, pipeline)
    await world.drain(pipeline)
    stored = await world.documents.get(owner.id, document.id)
    assert stored.lane is Lane.YELLOW
    assert stored.contact_id is None
    log = await world.documents.processing_log(owner.id, document.id)
    classified = next(entry for entry in log if entry.step is Step.CLASSIFY)
    assert classified.result.outcome is Outcome.UNCERTAIN
    assert (classified.result.reason or "").startswith("the master data changed during the step")
