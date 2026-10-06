"""Ingest and processing: receive a document, run its steps as jobs, retry and reprocess."""

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from papiq.core.domain.documents import Document, Sha256
from papiq.core.domain.errors import (
    DuplicateDocumentError,
    PermissionDeniedError,
)
from papiq.core.domain.ids import DocumentId, DrawerId, JobId, UserId
from papiq.core.domain.jobs import Job
from papiq.core.domain.json_value import JsonObject
from papiq.core.domain.permissions import can_file_into, is_document_owner
from papiq.core.domain.pipeline import PIPELINE, Outcome, Step, StepResult, StepRun
from papiq.core.domain.users import User
from papiq.core.ports import Clock, ObjectStore, UnitOfWork, UnitOfWorkFactory
from papiq.core.services._access import load_actor, readable_document, visible_drawer

log = logging.getLogger(__name__)

STEP_JOB = "pipeline.step"
"""Job kind of a pipeline step; payload: document id, step, processing run."""


class StepExecutor(Protocol):
    """Does the work of one pipeline step (OCR, parsing, classification, ...).

    Runs outside any transaction and may take long. Returns what the step decided; raising an
    exception counts as a failed attempt and is retried.
    """

    async def run(self, document: Document) -> StepResult: ...


class PlaceholderStep:
    """Stands in for steps whose logic comes in later milestones (M3, M5, M7)."""

    async def run(self, document: Document) -> StepResult:
        return StepResult(outcome=Outcome.OK)


@dataclass(frozen=True)
class RetryPolicy:
    """Automatic retries of a step that raised: `max_attempts` in total, waiting
    `delay * 2^(attempt-1)` before the next one."""

    max_attempts: int = 3
    delay: timedelta = timedelta(seconds=30)

    def next_run(self, attempts: int, now: datetime) -> datetime | None:
        if attempts >= self.max_attempts:
            return None
        return now + self.delay * (1 << max(attempts - 1, 0))


DEFAULT_RETRY = RetryPolicy()


def original_key(sha256: Sha256) -> str:
    """Object key of an original; identical content is stored once."""
    return f"originals/{sha256.hex}"


class PipelineService:
    def __init__(
        self,
        uow: UnitOfWorkFactory,
        clock: Clock,
        object_store: ObjectStore,
        executors: Mapping[Step, StepExecutor],
        *,
        pipeline_version: str,
        retry: RetryPolicy = DEFAULT_RETRY,
        lease: timedelta = timedelta(minutes=10),
    ) -> None:
        missing = set(PIPELINE[1:]) - set(executors)
        if missing:
            raise ValueError(f"no executor for steps: {', '.join(sorted(missing))}")
        self._uow = uow
        self._clock = clock
        self._store = object_store
        self._executors = dict(executors)
        self._version = pipeline_version
        self._retry = retry
        self._lease = lease

    # --- ingest ---------------------------------------------------------------------------------

    async def receive(
        self,
        actor: UserId,
        data: bytes,
        *,
        filename: str,
        media_type: str,
        drawer: DrawerId | None = None,
    ) -> Document:
        """Store the original, then create the document, its first events and the OCR job in
        one transaction. Without `drawer` the document goes to the owner's default drawer;
        otherwise the owner needs write access to it. A file the owner already has is rejected
        with DuplicateDocumentError."""
        started = self._clock.now()
        sha256 = Sha256.of(data)
        async with self._uow() as uow:
            target = await self._target_drawer(uow, actor, drawer, sha256)
        await self._store.put(original_key(sha256), data, content_type=media_type)

        now = self._clock.now()
        result = StepResult(outcome=Outcome.OK, output={"sha256": sha256.hex, "size": len(data)})
        async with self._uow() as uow:
            # Check again: rights or a duplicate may have changed while the file was stored.
            await self._target_drawer(uow, actor, target, sha256)
            document = Document.receive(
                owner_id=actor,
                drawer_id=target,
                sha256=sha256,
                original_filename=filename,
                media_type=media_type,
                result=result,
                now=now,
            )
            await uow.documents.add(document)
            await self._log(uow, document.id, Step.RECEIVE, 1, result, started, now)
            await _enqueue_step(uow, document, now)
            await uow.outbox.add(document.pull_events())
            await uow.commit()
        return document

    async def _target_drawer(
        self, uow: UnitOfWork, actor: UserId, drawer: DrawerId | None, sha256: Sha256
    ) -> DrawerId:
        user = await load_actor(uow, actor)
        existing = await uow.documents.find_by_sha256(actor, sha256)
        if existing is not None:
            raise DuplicateDocumentError(existing.id)
        if drawer is None:
            return (await uow.drawers.get_default(actor)).id
        target = await visible_drawer(uow, user, drawer)
        if not can_file_into(user, target):
            raise PermissionDeniedError(f"no write access to drawer {drawer}")
        return target.id

    # --- retry and reprocess --------------------------------------------------------------------

    async def retry(self, actor: UserId, id: DocumentId) -> Document:
        """Owner only: repeat the failed step and continue from there."""
        async with self._uow() as uow:
            document = await _owned_document(uow, await load_actor(uow, actor), id)
            document.retry(self._clock.now())
            await self._restart(uow, document)
        return document

    async def reprocess_from(self, actor: UserId, id: DocumentId, step: Step) -> Document:
        """Owner only: discard the results from `step` on and process again from there."""
        async with self._uow() as uow:
            document = await _owned_document(uow, await load_actor(uow, actor), id)
            document.reprocess_from(step, self._clock.now())
            await self._restart(uow, document)
        return document

    async def _restart(self, uow: UnitOfWork, document: Document) -> None:
        await uow.documents.update(document)
        await _enqueue_step(uow, document, self._clock.now())
        await uow.outbox.add(document.pull_events())
        await uow.commit()

    # --- worker ---------------------------------------------------------------------------------

    async def run_next_job(self) -> bool:
        """Claim the next due step job and run it. Returns False if no job was due.

        The step's work runs outside any transaction; its result, the processing log entry,
        the events and the next step's job are stored in one transaction with the job's
        completion. A job whose step is no longer due (stale) is just completed.
        """
        async with self._uow() as uow:
            job = await uow.jobs.claim(now=self._clock.now(), lease=self._lease, kinds=[STEP_JOB])
            await uow.commit()
        if job is None:
            return False

        document_id, step, run = _parse_payload(job.payload)
        async with self._uow() as uow:
            document = await uow.documents.find(document_id)
        if document is None or not document.is_awaiting(step, run):
            await self._finish_stale(job)
            return True

        started = self._clock.now()
        try:
            result = await self._executors[step].run(document)
        except Exception as error:
            reason = f"{type(error).__name__}: {error}"
            retry_at = self._retry.next_run(job.attempts, self._clock.now())
            log.warning(
                "pipeline step failed",
                extra={"document_id": str(document_id), "step": step, "attempt": job.attempts},
                exc_info=True,
            )
            if retry_at is not None:
                async with self._uow() as uow:
                    await uow.jobs.reschedule(job.id, run_at=retry_at, error=reason)
                    await uow.commit()
                return True
            result = StepResult(outcome=Outcome.FAILED, reason=reason)
            await self._record(job, document_id, step, run, result, started, failed=True)
            return True

        await self._record(job, document_id, step, run, result, started, failed=False)
        return True

    async def _record(
        self,
        job: Job,
        document_id: DocumentId,
        step: Step,
        run: int,
        result: StepResult,
        started: datetime,
        *,
        failed: bool,
    ) -> None:
        now = self._clock.now()
        async with self._uow() as uow:
            document = await uow.documents.find(document_id)
            if document is None or not document.is_awaiting(step, run):
                await uow.jobs.complete(job.id)
                await uow.commit()
                return
            next_step = document.record_result(step, run, result, now)
            await uow.documents.update(document)
            await self._log(uow, document.id, step, run, result, started, now)
            if next_step is not None:
                await _enqueue_step(uow, document, now)
            if failed:
                await uow.jobs.fail(job.id, error=result.reason or "failed")
            else:
                await uow.jobs.complete(job.id)
            await uow.outbox.add(document.pull_events())
            await uow.commit()

    async def _finish_stale(self, job: Job) -> None:
        async with self._uow() as uow:
            await uow.jobs.complete(job.id)
            await uow.commit()

    async def _log(
        self,
        uow: UnitOfWork,
        document: DocumentId,
        step: Step,
        run: int,
        result: StepResult,
        started: datetime,
        now: datetime,
    ) -> None:
        await uow.processing_log.append(
            StepRun(
                document_id=document,
                step=step,
                run=run,
                result=result,
                pipeline_version=self._version,
                started_at=started,
                duration=now - started,
            )
        )


async def _owned_document(uow: UnitOfWork, user: User, id: DocumentId) -> Document:
    document, _ = await readable_document(uow, user, id)
    if not is_document_owner(user, document):
        raise PermissionDeniedError(f"only the owner controls processing of document {id}")
    return document


async def _enqueue_step(uow: UnitOfWork, document: Document, now: datetime) -> JobId | None:
    step = document.processing.current_step
    assert step is not None  # called while processing
    run = document.processing.run
    payload: JsonObject = {"document_id": str(document.id), "step": step.value, "run": run}
    return await uow.jobs.enqueue(
        STEP_JOB, payload, run_at=now, dedup_key=f"{document.id}:{run}:{step.value}"
    )


def _parse_payload(payload: JsonObject) -> tuple[DocumentId, Step, int]:
    run = payload["run"]
    if not isinstance(run, int):
        raise ValueError(f"invalid step job payload {payload!r}")
    return DocumentId(UUID(str(payload["document_id"]))), Step(str(payload["step"])), run
