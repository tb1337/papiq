"""Request and response bodies of the API."""

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from papiq.core.domain.documents import Document
from papiq.core.domain.pipeline import (
    PIPELINE,
    Lane,
    Outcome,
    ProcessingStatus,
    Step,
    StepRun,
)

ReprocessStep = StrEnum(  # type: ignore[misc]
    "ReprocessStep", {step.name: step.value for step in PIPELINE[1:]}
)
"""Steps processing can restart from; receiving cannot be repeated."""


class DocumentAccepted(BaseModel):
    id: UUID
    status_url: str = Field(description="Where to follow the processing.")


class Processing(BaseModel):
    status: ProcessingStatus = Field(
        description="`processing`: `current_step` is due; `completed`; `failed` at `current_step`."
    )
    current_step: Step | None
    run: int = Field(description="Grows with every retry or reprocessing.")
    outcomes: dict[Step, Outcome] = Field(description="Results of the current run.")


class DocumentStatus(BaseModel):
    id: UUID
    title: str
    original_filename: str
    media_type: str = Field(examples=["application/pdf"])
    drawer_id: UUID
    lane: Lane | None = Field(description="None while processing runs.")
    processing: Processing
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, document: Document) -> "DocumentStatus":
        state = document.processing
        return cls(
            id=document.id,
            title=document.title,
            original_filename=document.original_filename,
            media_type=document.media_type,
            drawer_id=document.drawer_id,
            lane=document.lane,
            processing=Processing(
                status=state.status,
                current_step=state.current_step,
                run=state.run,
                outcomes=dict(state.outcomes),
            ),
            created_at=document.created_at,
            updated_at=document.updated_at,
        )


class LogEntry(BaseModel):
    """One execution of one step."""

    step: Step
    run: int
    outcome: Outcome
    reason: str | None
    confidence: float | None
    model_version: str | None = Field(examples=["ocrmypdf 17.13.0, tesseract 5.5.0"])
    input: dict[str, Any]
    output: dict[str, Any]
    pipeline_version: str
    started_at: datetime
    duration_ms: float

    @classmethod
    def of(cls, run: StepRun) -> "LogEntry":
        result = run.result
        return cls(
            step=run.step,
            run=run.run,
            outcome=result.outcome,
            reason=result.reason,
            confidence=result.confidence,
            model_version=result.model_version,
            input=dict(result.input),
            output=dict(result.output),
            pipeline_version=run.pipeline_version,
            started_at=run.started_at,
            duration_ms=run.duration.total_seconds() * 1000,
        )


class ReprocessRequest(BaseModel):
    from_step: ReprocessStep = Field(
        description="Discard the results from this step on and process again from there."
    )


class Health(BaseModel):
    status: Literal["ok", "unavailable"]
    checks: dict[str, Literal["ok", "failed"]] = Field(
        examples=[{"database": "ok", "object_store": "ok"}]
    )


class EventMessage(BaseModel):
    """A domain event, thin: fetch the document for details. Fields beyond `type`, `id`,
    `occurred_at` and `document_id` depend on the type."""

    model_config = {"extra": "allow"}

    type: str = Field(examples=["document.step_completed"])
    id: UUID
    occurred_at: datetime
    document_id: UUID
