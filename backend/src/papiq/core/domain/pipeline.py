"""Ingest pipeline: steps, step results, processing state and lanes."""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

from papiq.core.domain.errors import ValidationError
from papiq.core.domain.ids import DocumentId
from papiq.core.domain.json_value import JsonObject
from papiq.core.domain.validation import require_utc


class Step(StrEnum):
    """Pipeline steps in their fixed order."""

    RECEIVE = "receive"
    OCR = "ocr"
    PARSE = "parse"
    CLASSIFY = "classify"
    EXTRACT_ATTRIBUTES = "extract_attributes"
    APPLY_RULES = "apply_rules"
    FILE = "file"

    @property
    def position(self) -> int:
        return PIPELINE.index(self)

    @property
    def next(self) -> "Step | None":
        position = self.position + 1
        return PIPELINE[position] if position < len(PIPELINE) else None


PIPELINE: tuple[Step, ...] = tuple(Step)


class Outcome(StrEnum):
    OK = "ok"
    UNCERTAIN = "uncertain"  # a person has to confirm
    FAILED = "failed"  # after all retries; a person has to intervene


class Lane(StrEnum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"

    @classmethod
    def from_outcomes(cls, outcomes: Iterable[Outcome]) -> "Lane":
        """The worst outcome decides: any failure is red, any uncertainty yellow."""
        found = set(outcomes)
        if Outcome.FAILED in found:
            return cls.RED
        if Outcome.UNCERTAIN in found:
            return cls.YELLOW
        return cls.GREEN


@dataclass(frozen=True, kw_only=True)
class StepResult:
    """What a step decided. A reason is required unless the step went through cleanly."""

    outcome: Outcome
    reason: str | None = None
    confidence: float | None = None  # 0..1, derived from verifiable facts where possible
    model_version: str | None = None
    input: JsonObject = field(default_factory=dict)
    output: JsonObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.outcome is not Outcome.OK and not (self.reason and self.reason.strip()):
            raise ValidationError(f"a {self.outcome} step result needs a reason")
        if self.confidence is not None and not 0 <= self.confidence <= 1:
            raise ValidationError(f"confidence must be between 0 and 1, got {self.confidence}")


@dataclass(frozen=True, kw_only=True)
class StepRun:
    """Processing log entry: one execution of one step for one document."""

    document_id: DocumentId
    step: Step
    run: int
    result: StepResult
    pipeline_version: str
    started_at: datetime
    duration: timedelta

    def __post_init__(self) -> None:
        object.__setattr__(self, "started_at", require_utc(self.started_at, "started_at"))
        if self.duration < timedelta(0):
            raise ValidationError("duration must not be negative")
        if self.run < 1:
            raise ValidationError("run starts at 1")


class ProcessingStatus(StrEnum):
    PROCESSING = "processing"  # `current_step` is due
    COMPLETED = "completed"  # all steps done
    FAILED = "failed"  # stopped at `current_step`
    REVIEW = "review"  # uncertain results; waits before `current_step` (filing) for the owner


@dataclass(kw_only=True)
class Processing:
    """Processing state of a document.

    `run` counts processing runs; it grows with every retry or reprocessing, so jobs of an
    earlier run are recognised as stale. `outcomes` holds the results of the current run.
    """

    status: ProcessingStatus
    current_step: Step | None
    run: int
    outcomes: dict[Step, Outcome] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if (self.status is ProcessingStatus.COMPLETED) != (self.current_step is None):
            raise ValidationError("only completed processing has no current step")
