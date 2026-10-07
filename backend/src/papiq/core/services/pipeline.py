"""Ingest and processing: receive a document, run its steps as jobs, retry and reprocess."""

import asyncio
import hashlib
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol
from uuid import UUID

from papiq.core.domain import media_types
from papiq.core.domain.documents import Document, Sha256
from papiq.core.domain.errors import (
    ConcurrencyError,
    ConflictError,
    DuplicateDocumentError,
    PermissionDeniedError,
    UnprocessableDocumentError,
    UnsupportedMediaTypeError,
)
from papiq.core.domain.ids import DocumentId, DrawerId, JobId, UserId
from papiq.core.domain.jobs import Job, JobStatus
from papiq.core.domain.json_value import JsonObject
from papiq.core.domain.permissions import can_file_into, is_document_owner
from papiq.core.domain.pipeline import PIPELINE, Outcome, Step, StepResult, StepRun
from papiq.core.domain.users import User
from papiq.core.ports import Clock, ObjectStore, UnitOfWork, UnitOfWorkFactory
from papiq.core.services._access import load_actor, readable_document, visible_drawer
from papiq.core.services.objects import original_key

log = logging.getLogger(__name__)

# Attempts to store a step result while the document is changed concurrently.
_RECORD_ATTEMPTS = 3

STEP_JOB = "pipeline.step"
"""Job kind of a pipeline step; payload: document id, step, processing run."""


class StepExecutor(Protocol):
    """Does the work of one pipeline step (OCR, parsing, classification, ...).

    Runs outside any transaction and may take long. Returns what the step decided; raising an
    exception counts as a failed attempt and is retried, except UnprocessableDocumentError,
    which fails the step at once.
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


@dataclass(frozen=True)
class IncomingFile:
    """A received file in a local temporary location. The receiving adapter computes the
    SHA-256 and size while it writes the file, so the content is read only once."""

    path: Path
    sha256: Sha256
    size: int

    @classmethod
    def of(cls, path: Path) -> "IncomingFile":
        """Hash an existing file (blocking; for tools and tests)."""
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as file:
            while chunk := file.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
        return cls(path=path, sha256=Sha256(digest.hexdigest()), size=size)


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
        file: IncomingFile,
        *,
        filename: str,
        drawer: DrawerId | None = None,
    ) -> Document:
        """Store the original, then create the document, its first events and the OCR job in
        one transaction.

        The media type is recognised from the content; an unsupported type raises
        UnsupportedMediaTypeError and nothing is stored. Without `drawer` the document goes to
        the owner's default drawer; otherwise the owner needs write access to it. A file the
        owner already has is rejected with DuplicateDocumentError, also if the same file arrives
        twice at the same moment. An
        original that is stored already (another owner has the same file) is not stored again.
        """
        started = self._clock.now()
        sha256 = file.sha256
        media_type = media_types.detect(await asyncio.to_thread(_head, file.path))
        if media_type is None:
            raise UnsupportedMediaTypeError(
                f"unsupported file type; supported: {', '.join(media_types.SUPPORTED)}"
            )
        async with self._uow() as uow:
            target = await self._target_drawer(uow, actor, drawer, sha256)
        key = original_key(sha256)
        if not await self._store.exists(key):
            await self._store.upload(key, file.path, content_type=media_type)

        result = StepResult(
            outcome=Outcome.OK,
            output={"sha256": sha256.hex, "size": file.size, "media_type": media_type},
        )
        try:
            return await self._create(actor, target, sha256, filename, media_type, result, started)
        except ConflictError:
            # The same file arrived twice at the same moment; the other upload won.
            async with self._uow() as uow:
                existing = await uow.documents.find_by_sha256(actor, sha256)
            if existing is None:
                raise
            raise DuplicateDocumentError(existing.id) from None

    async def _create(
        self,
        actor: UserId,
        target: DrawerId,
        sha256: Sha256,
        filename: str,
        media_type: str,
        result: StepResult,
        started: datetime,
    ) -> Document:
        now = self._clock.now()
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
        completion. A job whose step is no longer due (stale) is just completed. A job that
        was claimed more often than the retry policy allows (the worker died or overran its
        lease each time) fails without running again.

        If the caller is cancelled after the claim (the worker shuts down), the job is released
        at once to run again, instead of waiting for its lease to expire. The release still
        counts as an attempt.
        """
        async with self._uow() as uow:
            job = await uow.jobs.claim(now=self._clock.now(), lease=self._lease, kinds=[STEP_JOB])
            await uow.commit()
        if job is None:
            return False
        try:
            await self._run_claimed(job)
        except asyncio.CancelledError:
            await asyncio.shield(self._release(job))
            raise
        return True

    async def _run_claimed(self, job: Job) -> None:
        try:
            document_id, step, run = _parse_payload(job.payload)
        except (KeyError, TypeError, ValueError) as error:
            log.error("invalid step job", extra={"job_id": str(job.id), "error": str(error)})
            await self._finish(job, error=f"invalid payload: {error}")
            return
        async with self._uow() as uow:
            document = await uow.documents.find(document_id)
        if document is None or not document.is_awaiting(step, run):
            await self._finish(job)
            return

        started = self._clock.now()
        if job.attempts > self._retry.max_attempts:
            reason = (
                f"step did not finish in {self._retry.max_attempts} attempts "
                "(the worker stopped or ran out of time each time)"
            )
            await self._record(job, document_id, step, run, _failed(reason), started)
            return
        try:
            result = await self._executors[step].run(document)
        except UnprocessableDocumentError as error:
            result = _failed(str(error))
        except Exception as error:
            reason = f"{type(error).__name__}: {error}"
            retry_at = self._retry.next_run(job.attempts, self._clock.now())
            log.warning(
                "pipeline step failed",
                extra={"document_id": str(document_id), "step": step, "attempt": job.attempts},
                exc_info=True,
            )
            if retry_at is None:
                await self._record(job, document_id, step, run, _failed(reason), started)
                return
            try:
                async with self._uow() as uow:
                    await uow.jobs.reschedule(job, run_at=retry_at, error=reason)
                    await uow.commit()
            except ConcurrencyError:
                log.warning("lost the claim of a step job", extra={"job_id": str(job.id)})
            return

        await self._record(job, document_id, step, run, result, started)

    async def _record(
        self,
        job: Job,
        document_id: DocumentId,
        step: Step,
        run: int,
        result: StepResult,
        started: datetime,
    ) -> None:
        """Store a step result. A concurrent change of the document (e.g. a metadata edit) is
        retried; if the job's claim was lost, another worker owns the step and nothing is
        stored."""
        for attempt in range(1, _RECORD_ATTEMPTS + 1):
            try:
                await self._record_once(job, document_id, step, run, result, started)
                return
            except ConcurrencyError:
                if not await self._still_claimed(job):
                    log.warning("lost the claim of a step job", extra={"job_id": str(job.id)})
                    return
                if attempt == _RECORD_ATTEMPTS:
                    raise

    async def _record_once(
        self,
        job: Job,
        document_id: DocumentId,
        step: Step,
        run: int,
        result: StepResult,
        started: datetime,
    ) -> None:
        now = self._clock.now()
        async with self._uow() as uow:
            document = await uow.documents.find(document_id)
            if document is None or not document.is_awaiting(step, run):
                await uow.jobs.complete(job)
                await uow.commit()
                return
            next_step = document.record_result(step, run, result, now)
            await uow.documents.update(document)
            await self._log(uow, document.id, step, run, result, started, now)
            if next_step is not None:
                await _enqueue_step(uow, document, now)
            if result.outcome is Outcome.FAILED:
                await uow.jobs.fail(job, error=result.reason or "failed")
            else:
                await uow.jobs.complete(job)
            await uow.outbox.add(document.pull_events())
            await uow.commit()

    async def _release(self, job: Job) -> None:
        """Let an interrupted job run again at once. Nothing to do if it was finished or lost
        its claim meanwhile."""
        try:
            async with self._uow() as uow:
                await uow.jobs.reschedule(
                    job, run_at=self._clock.now(), error="interrupted: the worker stopped"
                )
                await uow.commit()
        except ConcurrencyError:
            pass
        except Exception:
            log.warning(
                "could not release an interrupted job",
                extra={"job_id": str(job.id)},
                exc_info=True,
            )

    async def _still_claimed(self, job: Job) -> bool:
        async with self._uow() as uow:
            current = await uow.jobs.get(job.id)
        return current.status is JobStatus.RUNNING and current.attempts == job.attempts

    async def _finish(self, job: Job, *, error: str | None = None) -> None:
        """End a job that has nothing (more) to do: completed if stale, failed with `error`."""
        try:
            async with self._uow() as uow:
                if error is None:
                    await uow.jobs.complete(job)
                else:
                    await uow.jobs.fail(job, error=error)
                await uow.commit()
        except ConcurrencyError:
            log.warning("lost the claim of a step job", extra={"job_id": str(job.id)})

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


def _failed(reason: str) -> StepResult:
    return StepResult(outcome=Outcome.FAILED, reason=reason)


def _head(path: Path) -> bytes:
    with path.open("rb") as file:
        return file.read(media_types.SNIFF_SIZE)


def _parse_payload(payload: JsonObject) -> tuple[DocumentId, Step, int]:
    run = payload["run"]
    if not isinstance(run, int):
        raise ValueError(f"invalid step job payload {payload!r}")
    return DocumentId(UUID(str(payload["document_id"]))), Step(str(payload["step"])), run
