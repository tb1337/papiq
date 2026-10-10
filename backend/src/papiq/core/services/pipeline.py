"""Ingest and processing: receive a document, run its steps as jobs, retry and reprocess."""

import asyncio
import hashlib
import logging
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol
from uuid import UUID

from papiq.core.domain import media_types
from papiq.core.domain.documents import Channel, Document, DocumentChanges, Sha256
from papiq.core.domain.errors import (
    ConcurrencyError,
    ConflictError,
    DuplicateDocumentError,
    NotFoundError,
    OpenFieldsError,
    PermissionDeniedError,
    UnprocessableDocumentError,
    UnsupportedMediaTypeError,
    ValidationError,
)
from papiq.core.domain.ids import DocumentId, DrawerId, JobId, TagId, UserId
from papiq.core.domain.imports import IMPORTED, ImportedMetadata
from papiq.core.domain.jobs import Job, JobStatus
from papiq.core.domain.json_value import JsonObject
from papiq.core.domain.permissions import can_control_document, can_file_into
from papiq.core.domain.pipeline import PIPELINE, Outcome, Step, StepResult, StepRun
from papiq.core.domain.rule_engine import DRAWER, changed_fields
from papiq.core.domain.users import User
from papiq.core.ports import Clock, ObjectStore, UnitOfWork, UnitOfWorkFactory
from papiq.core.services._access import (
    check_references,
    load_actor,
    may_file,
    readable_document,
    visible_drawer,
)
from papiq.core.services.inbox import CHOSEN_DRAWER, confirmation, decide, open_steps
from papiq.core.services.objects import original_key
from papiq.core.services.rules.running import drawer_choice, person_record

log = logging.getLogger(__name__)

# Attempts to store a step result while the document is changed concurrently.
_RECORD_ATTEMPTS = 3

STEP_JOB = "pipeline.step"
"""Job kind of a pipeline step; payload: document id, step, processing run."""


class DeferredResult(Protocol):
    """A step result that is completed in the transaction that stores it, on the state that is
    stored (applying metadata, running rules)."""

    async def apply(self, uow: UnitOfWork, document: Document, now: datetime) -> StepResult: ...


@dataclass(frozen=True)
class MetadataResult:
    """A step result together with a change of the document's metadata (classification).
    Both are stored in the same transaction. If the change no longer fits (master data was
    removed meanwhile), nothing is changed and the step is uncertain. `add_tags` are added to
    the tags the document has when the result is stored, so tags set meanwhile stay."""

    result: StepResult
    changes: DocumentChanges
    add_tags: frozenset[TagId] = frozenset()

    async def apply(self, uow: UnitOfWork, document: Document, now: datetime) -> StepResult:
        changes = self.changes
        if not self.add_tags <= document.tag_ids:
            changes = replace(changes, tag_ids=frozenset(document.tag_ids | self.add_tags))
        try:
            await check_references(uow, changes)
            definitions = {item.id: item for item in await uow.fields.list_all()}
            document.apply_changes(changes, definitions, now)
        except (NotFoundError, ValidationError) as error:
            reason = f"the master data changed during the step; nothing was applied ({error})"
            return replace(self.result, outcome=Outcome.UNCERTAIN, reason=reason)
        return self.result


class StepExecutor(Protocol):
    """Does the work of one pipeline step (OCR, parsing, classification, ...).

    Runs outside any transaction and may take long. Returns what the step decided, or a result
    to complete when it is stored; raising an exception counts as a failed attempt and is
    retried, except UnprocessableDocumentError, which fails the step at once.
    """

    async def run(self, document: Document) -> StepResult | DeferredResult: ...


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
        channel: Channel = Channel.API,
        owner: UserId | None = None,
        imported: ImportedMetadata | None = None,
    ) -> Document:
        """Store the original, then create the document, its first events and the OCR job in
        one transaction.

        The media type is recognised from the content; an unsupported type raises
        UnsupportedMediaTypeError and nothing is stored. Without `drawer` the document goes to
        the owner's default drawer; otherwise the owner needs write access to it. A file the
        owner already has is rejected with DuplicateDocumentError, also if the same file arrives
        twice at the same moment. An
        original that is stored already (another owner has the same file) is not stored again.
        `channel`: how the document arrived (for rules).

        `owner`: an active admin may receive the document on behalf of another active user, who
        becomes its owner (the duplicate check, the default drawer and the write access to
        `drawer` are the owner's). `imported`: metadata of a document taken over from another
        system, for an active admin and the channel `migration` only; it is checked against the
        master data now and applied by the classification steps instead of the language model.
        """
        started = self._clock.now()
        sha256 = file.sha256
        media_type = media_types.detect(await asyncio.to_thread(_head, file.path))
        if media_type is None:
            raise UnsupportedMediaTypeError(
                f"unsupported file type; supported: {', '.join(media_types.SUPPORTED)}"
            )
        async with self._uow() as uow:
            owner = await self._owner(uow, actor, owner, channel, imported)
            target = await self._target_drawer(uow, owner, drawer, sha256)
            if imported is not None:
                await _check_imported(uow, owner, imported, started)
        key = original_key(sha256)
        if not await self._store.exists(key):
            await self._store.upload(key, file.path, content_type=media_type)

        output: JsonObject = {"sha256": sha256.hex, "size": file.size, "media_type": media_type}
        if owner != actor:
            output["uploaded_by"] = str(actor)
        if imported is not None:
            output[IMPORTED] = imported.to_json()
        result = StepResult(outcome=Outcome.OK, output=output)
        try:
            return await self._create(
                actor,
                owner,
                target,
                sha256,
                file.path,
                filename,
                media_type,
                channel,
                result,
                started,
                chosen=drawer is not None,
            )
        except ConflictError:
            # The same file arrived twice at the same moment; the other upload won.
            async with self._uow() as uow:
                existing = await uow.documents.find_by_sha256(owner, sha256)
            if existing is None:
                raise
            raise DuplicateDocumentError(existing.id) from None

    async def _create(
        self,
        actor: UserId,
        owner: UserId,
        target: DrawerId,
        sha256: Sha256,
        path: Path,
        filename: str,
        media_type: str,
        channel: Channel,
        result: StepResult,
        started: datetime,
        *,
        chosen: bool,
    ) -> Document:
        """`chosen`: the person chose the drawer; rules leave it."""
        now = self._clock.now()
        key = original_key(sha256)
        async with self._uow() as uow:
            # The removal of a deleted document's files may have taken the original meanwhile;
            # under the lock of its key, store it again if so.
            await uow.lock(key)
            if not await self._store.exists(key):
                await self._store.upload(key, path, content_type=media_type)
            # Check again: rights or a duplicate may have changed while the file was stored.
            await self._target_drawer(uow, owner, target, sha256)
            document = Document.receive(
                owner_id=owner,
                drawer_id=target,
                sha256=sha256,
                original_filename=filename,
                media_type=media_type,
                result=result,
                now=now,
                channel=channel,
            )
            await uow.documents.add(document)
            await self._log(uow, document.id, Step.RECEIVE, 1, result, started, now)
            if chosen:
                await uow.processing_log.append(
                    drawer_choice(
                        document,
                        step=Step.RECEIVE,
                        actor=actor,
                        trigger="upload",
                        version=self._version,
                        now=now,
                    )
                )
            await _enqueue_step(uow, document, now)
            await uow.outbox.add(document.pull_events())
            await uow.commit()
        return document

    async def _owner(
        self,
        uow: UnitOfWork,
        actor: UserId,
        owner: UserId | None,
        channel: Channel,
        imported: ImportedMetadata | None,
    ) -> UserId:
        """Who will own the document: the caller, or for an admin the user they name."""
        uploader = await load_actor(uow, actor)
        if imported is not None:
            if channel is not Channel.MIGRATION:
                raise ValidationError("metadata: only with the channel 'migration'")
            if not uploader.is_active_admin:
                raise PermissionDeniedError("only admins can give metadata with an upload")
        if owner is None or owner == actor:
            return actor
        if not uploader.is_active_admin:
            raise PermissionDeniedError("only admins can upload on behalf of another user")
        target = await uow.users.find(owner)
        if target is None or not target.active:
            raise NotFoundError("user", owner)
        return owner

    async def _target_drawer(
        self, uow: UnitOfWork, actor: UserId, drawer: DrawerId | None, sha256: Sha256
    ) -> DrawerId:
        """The drawer for a document of `actor` (its owner)."""
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
        """Owner or admin: repeat the failed step and continue from there."""
        async with self._uow() as uow:
            document = await _owned_document(uow, await load_actor(uow, actor), id)
            document.retry(self._clock.now())
            await self._restart(uow, document)
        return document

    async def reprocess_from(self, actor: UserId, id: DocumentId, step: Step) -> Document:
        """Owner or admin: discard the results from `step` on and process again from there."""
        async with self._uow() as uow:
            document = await _owned_document(uow, await load_actor(uow, actor), id)
            document.reprocess_from(step, self._clock.now())
            await self._restart(uow, document)
        return document

    async def confirm(
        self,
        actor: UserId,
        id: DocumentId,
        changes: DocumentChanges,
        *,
        accept_suggestions: bool = False,
        resume_at: Step = Step.APPLY_RULES,
        drawer: DrawerId | None = None,
    ) -> Document:
        """Owner or admin, for a document in the inbox: decide its open fields, apply `changes`
        (as a metadata change), move it into `drawer` (one the owner may write to; an admin
        chooses any drawer, and filing accepts it) and let processing continue from `resume_at`,
        up to filing.

        Each uncertain field of the steps before `resume_at`, and of the rules when processing
        resumes with them, needs a decision (see `inbox.decide`). The steps whose results the
        owner overruled get a log entry; so do the rules, with what the owner decided and
        changed: the rules that run next leave it as it is."""
        async with self._uow() as uow:
            user = await load_actor(uow, actor)
            document = await _owned_document(uow, user, id)
            log = await uow.processing_log.list_for(id)
            outcomes = dict(document.processing.outcomes)
            open = [
                item
                for item in open_steps(document, log)
                if item.step.position < resume_at.position
                or (item.step is Step.APPLY_RULES and resume_at is Step.APPLY_RULES)
            ]
            target = None if drawer is None else await visible_drawer(uow, user, drawer)
            if target is not None and not (user.is_active_admin or can_file_into(user, target)):
                raise PermissionDeniedError(f"no write access to drawer '{target.name}'")
            if target is None and not await may_file(
                uow, document, await uow.drawers.get(document.drawer_id)
            ):
                # Filing would stop again: someone has to choose another drawer.
                raise OpenFieldsError((DRAWER,))
            now = self._clock.now()
            tags_before = frozenset(document.tag_ids)
            overruled = document.confirm(resume_at, now)
            definitions = {item.id: item for item in await uow.fields.list_all()}
            decision = decide(
                open,
                document,
                changes,
                accept_suggestions=accept_suggestions,
                definitions=definitions,
                given=() if target is None else (DRAWER,),
            )
            await check_references(uow, decision.changes)
            document.apply_changes(decision.changes, definitions, now)
            if target is not None:
                document.move_to(target.id, now)
            run = document.processing.run
            for step in overruled:
                result = confirmation(step, outcomes.get(step), decision, user.id)
                await self._log(uow, id, step, run, result, now, now)
            result = confirmation(
                Step.APPLY_RULES, outcomes.get(Step.APPLY_RULES), decision, user.id
            )
            person = person_record(
                changed=changed_fields(decision.changes) | ({DRAWER} if target else set()),
                tags_before=tags_before,
                tags_after=document.tag_ids,
            )
            chosen = {} if target is None else {CHOSEN_DRAWER: str(target.id)}
            result = replace(result, output={**result.output, **person, **chosen})
            await self._log(uow, id, Step.APPLY_RULES, run, result, now, now)
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
        at once to run again, instead of waiting for its lease to expire; the interrupted claim
        does not count as an attempt.
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
        if job.tries > self._retry.max_attempts:
            reason = (
                f"step did not finish in {self._retry.max_attempts} attempts "
                "(the worker died or ran out of time each time)"
            )
            await self._record(job, document_id, step, run, _failed(reason), started)
            return
        try:
            outcome: StepResult | DeferredResult = await self._executors[step].run(document)
        except UnprocessableDocumentError as error:
            outcome = _failed(str(error))
        except Exception as error:
            reason = f"{type(error).__name__}: {error}"
            retry_at = self._retry.next_run(job.tries, self._clock.now())
            log.warning(
                "pipeline step failed",
                extra={"document_id": str(document_id), "step": step, "attempt": job.tries},
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

        await self._record(job, document_id, step, run, outcome, started)

    async def _record(
        self,
        job: Job,
        document_id: DocumentId,
        step: Step,
        run: int,
        result: StepResult | DeferredResult,
        started: datetime,
    ) -> None:
        """Store a step result (completing a deferred one on the stored state). A concurrent
        change of the document (e.g. a metadata edit) is retried; if the job's claim was lost,
        another worker owns the step and nothing is stored."""
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
        outcome: StepResult | DeferredResult,
        started: datetime,
    ) -> None:
        now = self._clock.now()
        async with self._uow() as uow:
            document = await uow.documents.find(document_id)
            if document is None or not document.is_awaiting(step, run):
                await uow.jobs.complete(job)
                await uow.commit()
                return
            if isinstance(outcome, StepResult):
                result = outcome
            else:
                result = await outcome.apply(uow, document, now)
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
                await uow.jobs.release(
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
    """The document, if the caller controls its processing: its owner or an admin."""
    document, _ = await readable_document(uow, user, id)
    if not can_control_document(user, document):
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


async def _check_imported(
    uow: UnitOfWork, owner: UserId, imported: ImportedMetadata, now: datetime
) -> None:
    """ValidationError if the metadata does not fit the master data: unknown contact, type or
    tag, or field values that do not fit their definition or the document type."""
    changes = replace(imported.classification(), tag_ids=imported.tag_ids)
    changes = replace(changes, fields=dict(imported.fields))
    try:
        await check_references(uow, changes)
        definitions = {item.id: item for item in await uow.fields.list_all()}
        scratch = Document.receive(
            owner_id=owner,
            drawer_id=DrawerId(owner),
            sha256=Sha256("0" * 64),
            original_filename="scratch",
            media_type="application/pdf",
            result=StepResult(outcome=Outcome.OK),
            now=now,
        )
        scratch.apply_changes(changes, definitions, now)
    except NotFoundError as error:
        raise ValidationError(f"metadata: {error}") from None


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
