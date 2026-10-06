from datetime import timedelta

import pytest

from papiq.core.domain.documents import Document, Sha256
from papiq.core.domain.drawers import ShareLevel
from papiq.core.domain.errors import (
    DuplicateDocumentError,
    InvalidTransitionError,
    NotFoundError,
    PermissionDeniedError,
)
from papiq.core.domain.events import (
    DocumentFiled,
    DocumentReceived,
    LaneChanged,
    StepCompleted,
)
from papiq.core.domain.jobs import JobStatus
from papiq.core.domain.pipeline import PIPELINE, Lane, Outcome, ProcessingStatus, Step
from papiq.core.domain.users import User
from papiq.core.services.pipeline import (
    STEP_JOB,
    PipelineService,
    PlaceholderStep,
    RetryPolicy,
    original_key,
)
from tests.builders import FAILED, NOW, UNCERTAIN
from tests.unit.services.conftest import Raises, Returns, World

PDF = b"%PDF-1.7 invoice"


async def receive(world: World, pipeline: PipelineService | None = None) -> tuple[User, Document]:
    owner = await world.user()
    document = await (pipeline or world.pipeline()).receive(
        owner.id, PDF, filename="invoice.pdf", media_type="application/pdf"
    )
    return owner, document


async def test_receive_stores_original_document_events_and_job(world: World) -> None:
    owner = await world.user()
    document = await world.pipeline().receive(
        owner.id, PDF, filename="invoice.pdf", media_type="application/pdf"
    )
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
        assert entry.result.output == {"sha256": document.sha256.hex, "size": len(PDF)}
        assert entry.pipeline_version == "test"
        job = await uow.jobs.claim(now=NOW, lease=timedelta(minutes=1))
    assert job is not None and job.kind == STEP_JOB
    assert job.payload == {"document_id": str(document.id), "step": "ocr", "run": 1}


async def test_pipeline_runs_to_green(world: World) -> None:
    owner = await world.user()
    document = await world.pipeline().receive(
        owner.id, PDF, filename="invoice.pdf", media_type="application/pdf"
    )
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
    assert await world.drain(pipeline) == 4
    stored = await world.documents.get(owner.id, document.id)
    assert stored.lane is Lane.YELLOW
    assert stored.processing.run == 2
    lanes = [(e.old, e.new) for e in world.events() if isinstance(e, LaneChanged)]
    assert lanes == [(Lane.GREEN, None), (None, Lane.YELLOW)]


async def test_retry_and_reprocess_are_for_the_owner(world: World) -> None:
    owner = await world.user()
    writer = await world.user()
    shared = await world.drawers.create(owner.id, "Shared")
    await world.drawers.share(owner.id, shared.id, writer.id, ShareLevel.READ_WRITE)
    document = await world.pipeline().receive(
        owner.id, PDF, filename="a.pdf", media_type="application/pdf", drawer=shared.id
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
        await world.pipeline().receive(
            owner.id, PDF, filename="again.pdf", media_type="application/pdf"
        )
    assert info.value.existing == document.id
    other = await world.user()
    copy = await world.pipeline().receive(
        other.id, PDF, filename="mine.pdf", media_type="application/pdf"
    )
    assert copy.sha256 == document.sha256
    assert copy.owner_id == other.id


async def test_receive_into_a_drawer_needs_write_access(world: World) -> None:
    owner, writer, reader = await world.user(), await world.user(), await world.user()
    shared = await world.drawers.create(owner.id, "Shared")
    await world.drawers.share(owner.id, shared.id, writer.id, ShareLevel.READ_WRITE)
    await world.drawers.share(owner.id, shared.id, reader.id, ShareLevel.READ)
    document = await world.pipeline().receive(
        writer.id, PDF, filename="a.pdf", media_type="application/pdf", drawer=shared.id
    )
    assert (document.owner_id, document.drawer_id) == (writer.id, shared.id)
    with pytest.raises(PermissionDeniedError):
        await world.pipeline().receive(
            reader.id, b"other", filename="b.pdf", media_type="application/pdf", drawer=shared.id
        )
    with pytest.raises(NotFoundError):
        await world.pipeline().receive(
            reader.id,
            b"other",
            filename="b.pdf",
            media_type="application/pdf",
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
    assert all(job.status is JobStatus.DONE for job in world.database.jobs.values())


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
