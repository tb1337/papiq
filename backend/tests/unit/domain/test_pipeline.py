from datetime import timedelta

import pytest

from papiq.core.domain.documents import Document
from papiq.core.domain.errors import InvalidTransitionError, ValidationError
from papiq.core.domain.events import DocumentFiled, LaneChanged, StepCompleted
from papiq.core.domain.ids import new_id
from papiq.core.domain.pipeline import (
    PIPELINE,
    Lane,
    Outcome,
    ProcessingStatus,
    Step,
    StepResult,
    StepRun,
)
from tests import builders
from tests.builders import FAILED, NOW, OK, UNCERTAIN


def status(document: Document) -> ProcessingStatus:
    return document.processing.status


def test_steps_have_a_fixed_order() -> None:
    assert PIPELINE == (
        Step.RECEIVE,
        Step.OCR,
        Step.PARSE,
        Step.CLASSIFY,
        Step.EXTRACT_FIELDS,
        Step.APPLY_RULES,
        Step.FILE,
    )
    assert Step.RECEIVE.next is Step.OCR
    assert Step.FILE.next is None


@pytest.mark.parametrize(
    ("outcomes", "lane"),
    [
        ([], Lane.GREEN),
        ([Outcome.OK, Outcome.OK], Lane.GREEN),
        ([Outcome.OK, Outcome.UNCERTAIN, Outcome.OK], Lane.YELLOW),
        ([Outcome.UNCERTAIN, Outcome.FAILED], Lane.RED),
        ([Outcome.OK, Outcome.FAILED], Lane.RED),
    ],
)
def test_lane_is_the_worst_outcome(outcomes: list[Outcome], lane: Lane) -> None:
    assert Lane.from_outcomes(outcomes) is lane


def test_step_result_needs_a_reason_unless_ok() -> None:
    StepResult(outcome=Outcome.OK)
    for outcome in (Outcome.UNCERTAIN, Outcome.FAILED):
        with pytest.raises(ValidationError, match="reason"):
            StepResult(outcome=outcome)
        with pytest.raises(ValidationError, match="reason"):
            StepResult(outcome=outcome, reason="  ")


@pytest.mark.parametrize("confidence", [-0.1, 1.1])
def test_confidence_is_between_zero_and_one(confidence: float) -> None:
    with pytest.raises(ValidationError, match="confidence"):
        StepResult(outcome=Outcome.OK, confidence=confidence)


def test_step_run_is_a_log_entry() -> None:
    document = builders.document(builders.user(), builders.drawer(builders.user()))
    run = StepRun(
        document_id=document.id,
        step=Step.OCR,
        run=1,
        result=UNCERTAIN,
        pipeline_version="0.1.0",
        started_at=NOW,
        duration=timedelta(seconds=3),
    )
    assert run.result.reason == "new contact"
    with pytest.raises(ValidationError):
        StepRun(
            document_id=document.id,
            step=Step.OCR,
            run=1,
            result=OK,
            pipeline_version="0.1.0",
            started_at=NOW,
            duration=timedelta(seconds=-1),
        )


def test_received_document_waits_for_ocr_without_lane() -> None:
    document = builders.document(builders.user(), builders.drawer(builders.user()))
    assert document.processing.status is ProcessingStatus.PROCESSING
    assert document.processing.current_step is Step.OCR
    assert document.processing.run == 1
    assert document.processing.outcomes == {Step.RECEIVE: Outcome.OK}
    assert document.lane is None
    assert document.title == "scan"


def test_steps_run_in_order_and_end_green() -> None:
    document = builders.document(builders.user(), builders.drawer(builders.user()))
    due = [Step.OCR]
    while (step := due[-1]) is not None:
        following = document.record_result(step, 1, OK, NOW)
        due.append(following)  # type: ignore[arg-type]
    assert due == [*PIPELINE[1:], None]
    assert document.processing.status is ProcessingStatus.COMPLETED
    assert document.processing.current_step is None
    assert document.lane is Lane.GREEN


def test_uncertain_step_continues_and_waits_yellow_before_filing() -> None:
    document = builders.run_pipeline(
        builders.document(builders.user(), builders.drawer(builders.user())),
        {Step.CLASSIFY: UNCERTAIN},
    )
    assert document.processing.status is ProcessingStatus.REVIEW
    assert document.processing.current_step is Step.FILE
    assert Step.APPLY_RULES in document.processing.outcomes
    assert Step.FILE not in document.processing.outcomes
    assert document.lane is Lane.YELLOW
    assert not document.is_awaiting(Step.FILE, document.processing.run)


def test_an_uncertain_filing_step_completes_yellow() -> None:
    document = builders.run_pipeline(
        builders.document(builders.user(), builders.drawer(builders.user())),
        {Step.FILE: UNCERTAIN},
    )
    assert document.processing.status is ProcessingStatus.COMPLETED
    assert document.lane is Lane.YELLOW


@pytest.mark.parametrize("resume_at", [Step.EXTRACT_FIELDS, Step.APPLY_RULES])
def test_confirming_a_yellow_document_resumes_and_files_it_green(resume_at: Step) -> None:
    document = builders.run_pipeline(
        builders.document(builders.user(), builders.drawer(builders.user())),
        {Step.OCR: UNCERTAIN, Step.CLASSIFY: UNCERTAIN},
    )
    run = document.processing.run
    document.pull_events()

    overruled = document.confirm(resume_at, NOW)

    assert overruled == (Step.OCR, Step.CLASSIFY)
    assert document.processing.run == run + 1
    assert document.processing.status is ProcessingStatus.PROCESSING
    assert document.processing.current_step is resume_at
    assert set(document.processing.outcomes) == set(PIPELINE[: resume_at.position])
    assert set(document.processing.outcomes.values()) == {Outcome.OK}
    assert document.lane is None
    (event,) = document.pull_events()
    assert isinstance(event, LaneChanged)
    assert (event.old, event.new) == (Lane.YELLOW, None)

    builders.run_pipeline(document)
    finished = document.processing
    assert finished.status is ProcessingStatus.COMPLETED
    assert document.lane is Lane.GREEN
    assert any(isinstance(event, DocumentFiled) for event in document.pull_events())


def test_confirming_a_red_document_takes_over_the_failed_steps() -> None:
    document = builders.run_pipeline(
        builders.document(builders.user(), builders.drawer(builders.user())),
        {Step.OCR: FAILED},
    )
    assert document.confirm(Step.APPLY_RULES, NOW) == (
        Step.OCR,
        Step.PARSE,
        Step.CLASSIFY,
        Step.EXTRACT_FIELDS,
    )
    builders.run_pipeline(document)
    assert document.lane is Lane.GREEN


def test_fields_are_only_extracted_again_from_a_parsed_text() -> None:
    document = builders.run_pipeline(
        builders.document(builders.user(), builders.drawer(builders.user())),
        {Step.PARSE: FAILED},
    )
    with pytest.raises(InvalidTransitionError, match="no text"):
        document.confirm(Step.EXTRACT_FIELDS, NOW)


@pytest.mark.parametrize("resume_at", [Step.OCR, Step.CLASSIFY, Step.FILE])
def test_confirmation_resumes_only_after_classification(resume_at: Step) -> None:
    document = builders.run_pipeline(
        builders.document(builders.user(), builders.drawer(builders.user())),
        {Step.CLASSIFY: UNCERTAIN},
    )
    with pytest.raises(InvalidTransitionError, match="resumes with"):
        document.confirm(resume_at, NOW)


def test_only_documents_in_the_inbox_are_confirmed() -> None:
    owner = builders.user()
    processing = builders.document(owner, builders.drawer(owner))
    green = builders.run_pipeline(builders.document(owner, builders.drawer(owner)))
    for document in (processing, green):
        with pytest.raises(InvalidTransitionError, match="not waiting for confirmation"):
            document.confirm(Step.APPLY_RULES, NOW)


def test_uncertain_results_are_not_filed_by_reprocessing() -> None:
    document = builders.run_pipeline(
        builders.document(builders.user(), builders.drawer(builders.user())),
        {Step.CLASSIFY: UNCERTAIN},
    )
    with pytest.raises(InvalidTransitionError, match="confirm them before filing"):
        document.reprocess_from(Step.FILE, NOW)
    document.reprocess_from(Step.APPLY_RULES, NOW)
    builders.run_pipeline(document)
    assert document.processing.status is ProcessingStatus.REVIEW


def test_failed_step_stops_processing_red() -> None:
    document = builders.run_pipeline(
        builders.document(builders.user(), builders.drawer(builders.user())),
        {Step.PARSE: FAILED},
    )
    assert document.processing.status is ProcessingStatus.FAILED
    assert document.processing.current_step is Step.PARSE
    assert Step.CLASSIFY not in document.processing.outcomes
    assert document.lane is Lane.RED


@pytest.mark.parametrize(
    ("step", "run"),
    [(Step.PARSE, 1), (Step.RECEIVE, 1), (Step.OCR, 2), (Step.OCR, 0)],
)
def test_only_the_due_step_of_the_current_run_is_accepted(step: Step, run: int) -> None:
    document = builders.document(builders.user(), builders.drawer(builders.user()))
    assert not document.is_awaiting(step, run)
    with pytest.raises(InvalidTransitionError, match="not due"):
        document.record_result(step, run, OK, NOW)


def test_completed_document_accepts_no_more_results() -> None:
    document = builders.run_pipeline(
        builders.document(builders.user(), builders.drawer(builders.user()))
    )
    with pytest.raises(InvalidTransitionError):
        document.record_result(Step.FILE, 1, OK, NOW)


def test_retry_repeats_the_failed_step_and_continues() -> None:
    document = builders.run_pipeline(
        builders.document(builders.user(), builders.drawer(builders.user())),
        {Step.CLASSIFY: FAILED},
    )
    assert document.retry(NOW) is Step.CLASSIFY
    assert document.processing.status is ProcessingStatus.PROCESSING
    assert document.processing.run == 2
    assert document.lane is None
    assert Step.CLASSIFY not in document.processing.outcomes
    assert document.processing.outcomes[Step.PARSE] is Outcome.OK
    assert not document.is_awaiting(Step.CLASSIFY, 1)

    builders.run_pipeline(document)
    assert status(document) is ProcessingStatus.COMPLETED
    assert document.lane is Lane.GREEN


def test_retry_needs_failed_processing() -> None:
    document = builders.document(builders.user(), builders.drawer(builders.user()))
    with pytest.raises(InvalidTransitionError, match="only failed"):
        document.retry(NOW)
    builders.run_pipeline(document)
    with pytest.raises(InvalidTransitionError, match="only failed"):
        document.retry(NOW)


def test_reprocess_from_a_step_discards_later_results() -> None:
    document = builders.run_pipeline(
        builders.document(builders.user(), builders.drawer(builders.user())),
        {Step.EXTRACT_FIELDS: UNCERTAIN},
    )
    assert document.lane is Lane.YELLOW
    assert document.reprocess_from(Step.CLASSIFY, NOW) is Step.CLASSIFY
    assert document.processing.run == 2
    assert set(document.processing.outcomes) == {Step.RECEIVE, Step.OCR, Step.PARSE}
    assert document.lane is None
    builders.run_pipeline(document)
    assert document.lane is Lane.GREEN


def test_reprocess_rules() -> None:
    document = builders.document(builders.user(), builders.drawer(builders.user()))
    with pytest.raises(InvalidTransitionError, match="being processed"):
        document.reprocess_from(Step.OCR, NOW)

    builders.run_pipeline(document, {Step.PARSE: FAILED})
    with pytest.raises(InvalidTransitionError, match="receive"):
        document.reprocess_from(Step.RECEIVE, NOW)
    with pytest.raises(InvalidTransitionError, match="cannot skip"):
        document.reprocess_from(Step.CLASSIFY, NOW)
    assert document.reprocess_from(Step.OCR, NOW) is Step.OCR


def test_events_follow_the_state_changes() -> None:
    document = builders.document(builders.user(), builders.drawer(builders.user()))
    builders.run_pipeline(document, {Step.CLASSIFY: UNCERTAIN})
    events = document.pull_events()
    completed = [event for event in events if isinstance(event, StepCompleted)]
    assert [event.step for event in completed] == list(PIPELINE[1:-1])
    assert completed[2].outcome is Outcome.UNCERTAIN
    assert not any(isinstance(event, DocumentFiled) for event in events)
    assert [type(event) for event in events[-2:]] == [StepCompleted, LaneChanged]
    lane_changed = events[-1]
    assert isinstance(lane_changed, LaneChanged)
    assert (lane_changed.old, lane_changed.new) == (None, Lane.YELLOW)
    assert all(event.document_id == document.id for event in events)
    assert document.pull_events() == []

    document.reprocess_from(Step.APPLY_RULES, NOW)
    (event,) = document.pull_events()
    assert isinstance(event, LaneChanged)
    assert (event.old, event.new) == (Lane.YELLOW, None)


def test_failure_records_lane_red_without_filing() -> None:
    document = builders.document(builders.user(), builders.drawer(builders.user()))
    builders.run_pipeline(document, {Step.OCR: FAILED})
    events = document.pull_events()
    assert [type(event) for event in events] == [StepCompleted, LaneChanged]
    assert not any(isinstance(event, DocumentFiled) for event in events)


def test_event_ids_are_unique() -> None:
    document = builders.document(builders.user(), builders.drawer(builders.user()))
    builders.run_pipeline(document)
    ids = [event.id for event in document.pull_events()]
    assert len(set(ids)) == len(ids)
    assert new_id() not in ids
