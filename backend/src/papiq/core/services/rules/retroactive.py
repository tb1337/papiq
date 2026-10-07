"""Applying a rule to existing documents, on request: a preview, then a background job.

- Who, on what: a user rule by its owner, on their own documents; a global rule by anyone, on
  the documents they may write to. An admin has no extra rights here. Documents the caller may
  not write to appear neither one by one nor as a count; documents in processing are left out.
- The preview evaluates the rule's current version on each document (its state, not a change)
  and lists the documents it would change, with conflicts: a field that has another value.
- The application pins one version. A background job works through the selected documents, one
  transaction each, checks the rights again, takes the rule's value where the person accepted
  the conflict, skips other conflicts, and logs every document. A forced review does not act
  (it would change who sees a document). After `BATCH` documents the job hands the worker back
  and continues in a new job, so processing and indexing do not wait.
"""

import asyncio
import logging
from collections.abc import Collection, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from uuid import UUID

from papiq.core.domain.documents import Document
from papiq.core.domain.errors import (
    ConcurrencyError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from papiq.core.domain.ids import DocumentId, RuleApplicationId, RuleId, UserId
from papiq.core.domain.jobs import Job
from papiq.core.domain.json_value import JsonValue
from papiq.core.domain.permissions import can_write_document
from papiq.core.domain.pipeline import Lane, Outcome, Step, StepResult, StepRun
from papiq.core.domain.rule_engine import Effect, Mode, Note
from papiq.core.domain.rules import Rule, RuleApplication, RuleScope
from papiq.core.domain.users import User
from papiq.core.ports import (
    Clock,
    DocumentFilter,
    ObjectStore,
    PatternMatcher,
    UnitOfWork,
    UnitOfWorkFactory,
)
from papiq.core.services._access import load_actor
from papiq.core.services.inbox import RULES_APPLY
from papiq.core.services.rules.management import visible_rule
from papiq.core.services.rules.running import (
    Prepared,
    RuleRun,
    apply_plan,
    checked_rules,
    prepare,
    provenance,
    run_rules,
)

log = logging.getLogger(__name__)

APPLY_JOB = "rules.apply"
"""Job kind of a rule application; payload: application id."""
BATCH = 25
"""Documents per job; the next job continues."""
SCAN = 200
"""Documents a preview page looks at, at most."""
_LANES = frozenset({Lane.GREEN, Lane.YELLOW, Lane.RED})


@dataclass(frozen=True)
class PreviewItem:
    """A document the rule would change: the changes, and conflicts (not applied unless
    accepted) and other actions that would not act."""

    document: Document
    effects: tuple[Effect, ...]
    conflicts: tuple[Note, ...]
    notes: tuple[Note, ...]


@dataclass(frozen=True)
class ApplyPreview:
    rule: Rule
    items: tuple[PreviewItem, ...]
    next_cursor: DocumentId | None


class RuleApplicationService:
    def __init__(
        self,
        uow: UnitOfWorkFactory,
        clock: Clock,
        store: ObjectStore,
        matcher: PatternMatcher,
        *,
        max_text: int,
        max_documents: int,
        pipeline_version: str,
        lease: timedelta = timedelta(minutes=10),
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._store = store
        self._matcher = matcher
        self._max_text = max_text
        self._max_documents = max_documents
        self._version = pipeline_version
        self._lease = lease

    # --- preview ---------------------------------------------------------------------------------

    async def preview(
        self,
        actor: UserId,
        id: RuleId,
        *,
        before: DocumentId | None = None,
        limit: int = 50,
    ) -> ApplyPreview:
        """The documents the rule's current version would change, newest first, from the page
        ending at `before` on. A page looks at `SCAN` documents at most: it may hold fewer than
        `limit` items while `next_cursor` says there are more to look at."""
        async with self._uow() as uow:
            user = await load_actor(uow, actor)
            rule = await _applicable_rule(uow, user, id)
            documents = await uow.documents.query_visible(
                actor, DocumentFilter(lanes=_LANES), before=before, limit=SCAN
            )
        items: list[PreviewItem] = []
        next_cursor = documents[-1].id if len(documents) == SCAN else None
        for document in documents:
            if len(items) == limit:
                next_cursor = items[-1].document.id
                break
            prepared = await prepare(
                self._store, self._matcher, [rule], document, max_text=self._max_text
            )
            async with self._uow() as uow:
                current = await uow.documents.find(document.id)
                if current is None or not await _candidate(uow, user, rule, current):
                    continue
                run = await self._evaluate(uow, rule, current, prepared, accept=False)
            if run is None:
                continue
            report = run.plan.reports[0]
            conflicts = tuple(note for note in report.notes if note.kind == "conflict")
            others = tuple(note for note in report.notes if note.kind != "conflict")
            if report.applied or conflicts:
                items.append(PreviewItem(current, tuple(report.applied), conflicts, others))
        return ApplyPreview(rule, tuple(items), next_cursor)

    # --- applying --------------------------------------------------------------------------------

    async def start(
        self,
        actor: UserId,
        id: RuleId,
        *,
        version: int,
        documents: Sequence[DocumentId],
        accept_conflicts: Collection[DocumentId] = (),
    ) -> RuleApplication:
        """Apply `version` of the rule to the selected documents in the background."""
        if len(documents) > self._max_documents:
            raise ValidationError(f"select at most {self._max_documents} documents")
        async with self._uow() as uow:
            user = await load_actor(uow, actor)
            rule = await _applicable_rule(uow, user, id)
            await uow.rules.get_version(rule.id, version)
            application = RuleApplication.create(
                rule_id=rule.id,
                rule_version=version,
                user_id=user.id,
                documents=tuple(documents),
                accept_conflicts=frozenset(accept_conflicts),
                now=self._clock.now(),
            )
            await uow.rule_applications.add(application)
            await _enqueue(uow, application.id, self._clock.now())
            await uow.commit()
        return application

    async def get(self, actor: UserId, id: RuleApplicationId) -> RuleApplication:
        """Only for the user who started it."""
        async with self._uow() as uow:
            await load_actor(uow, actor)
            application = await uow.rule_applications.find(id)
        if application is None or application.user_id != actor:
            raise NotFoundError("rule application", id)
        return application

    # --- worker ----------------------------------------------------------------------------------

    async def run_next_job(self) -> bool:
        """Claim and run a due application job. Returns False if none was due."""
        async with self._uow() as uow:
            job = await uow.jobs.claim(now=self._clock.now(), lease=self._lease, kinds=[APPLY_JOB])
            await uow.commit()
        if job is None:
            return False
        try:
            await self._run(job)
        except asyncio.CancelledError:
            await asyncio.shield(self._give_back(job))
            raise
        except ConcurrencyError:
            log.warning("lost the claim of a rule job", extra={"job_id": str(job.id)})
        return True

    async def _run(self, job: Job) -> None:
        try:
            id = RuleApplicationId(UUID(str(job.payload["application_id"])))
        except (KeyError, ValueError) as error:
            async with self._uow() as uow:
                await uow.jobs.fail(job, error=f"invalid payload: {error}")
                await uow.commit()
            return
        for _ in range(BATCH):
            done = await self._next_document(id)
            if done:
                async with self._uow() as uow:
                    await uow.jobs.complete(job)
                    await uow.commit()
                return
        async with self._uow() as uow:
            await uow.jobs.complete(job)
            await _enqueue(uow, id, self._clock.now())
            await uow.commit()

    async def _next_document(self, id: RuleApplicationId) -> bool:
        """Apply the rule to the next document of the application; True if it is finished."""
        async with self._uow() as uow:
            application = await uow.rule_applications.find(id)
            if application is None or application.finished_at is not None:
                return True
            if not application.remaining:
                application.finish(self._clock.now())
                await uow.rule_applications.update(application)
                await uow.commit()
                return True
            document = await uow.documents.find(application.remaining[0])
            rule = await uow.rules.find(application.rule_id)
            user = await uow.users.find(application.user_id)
            if rule is None or rule.deleted_at is not None or user is None or not user.active:
                application.finish(self._clock.now(), error="the rule or its user is gone")
                await uow.rule_applications.update(application)
                await uow.commit()
                return True
            rule = replace(
                rule, current=await uow.rules.get_version(rule.id, application.rule_version)
            )
        prepared = Prepared()
        if document is not None:
            prepared = await prepare(
                self._store, self._matcher, [rule], document, max_text=self._max_text
            )
        async with self._uow() as uow:
            application = await uow.rule_applications.get(id)
            document_id = application.remaining[0]
            outcome = await self._apply_one(uow, user, rule, document_id, prepared, application)
            application.record(document_id, outcome)
            await uow.rule_applications.update(application)
            await uow.commit()
        return False

    async def _apply_one(
        self,
        uow: UnitOfWork,
        user: User,
        rule: Rule,
        id: DocumentId,
        prepared: Prepared,
        application: RuleApplication,
    ) -> str:
        document = await uow.documents.find(id)
        if document is None or not await _candidate(uow, user, rule, document):
            return "not available"
        run = await self._evaluate(
            uow, rule, document, prepared, accept=id in application.accept_conflicts
        )
        if run is None or not run.plan.effects:
            return "unchanged"
        now = self._clock.now()
        definitions = {item.id: item for item in await uow.attributes.list_all()}
        problem = apply_plan(document, run.plan, definitions, now)
        if problem is not None:
            return problem
        await uow.processing_log.append(
            StepRun(
                document_id=document.id,
                step=Step.APPLY_RULES,
                run=document.processing.run,
                result=StepResult(
                    outcome=Outcome.OK,
                    model_version=RULES_APPLY,
                    input={
                        "trigger": "apply",
                        "actor": str(user.id),
                        "application_id": str(application.id),
                        "rules": checked_rules([rule]),
                    },
                    output=run.output(),
                ),
                pipeline_version=self._version,
                started_at=now,
                duration=now - now,
            )
        )
        await uow.documents.update(document)
        await uow.outbox.add(document.pull_events())
        return "applied"

    async def _evaluate(
        self,
        uow: UnitOfWork,
        rule: Rule,
        document: Document,
        prepared: Prepared,
        *,
        accept: bool,
    ) -> RuleRun | None:
        """The rule on the document; None if it does not hold."""
        owner = await uow.users.get(document.owner_id)
        definitions = {item.id: item for item in await uow.attributes.list_all()}
        run = await run_rules(
            uow,
            self._matcher,
            mode=Mode.RETROACTIVE,
            document=document,
            owner=owner,
            rules=[rule],
            prepared=prepared,
            origin=provenance(await uow.processing_log.list_for(document.id), document),
            definitions=definitions,
            accept_conflicts=accept,
        )
        return run if run.plan.reports else None

    async def _give_back(self, job: Job) -> None:
        try:
            async with self._uow() as uow:
                await uow.jobs.release(job, run_at=self._clock.now(), error="worker stopped")
                await uow.commit()
        except ConcurrencyError:
            pass


async def _applicable_rule(uow: UnitOfWork, user: User, id: RuleId) -> Rule:
    """A user rule its owner applies; a global rule anyone applies."""
    rule = await visible_rule(uow, user, id)
    if rule.deleted_at is not None:
        raise NotFoundError("rule", id)
    if rule.scope is RuleScope.USER and rule.owner_id != user.id:
        raise PermissionDeniedError("only the owner applies a user rule")
    return rule


async def _candidate(uow: UnitOfWork, user: User, rule: Rule, document: Document) -> bool:
    """The caller may write to the document, it is not being processed, and a user rule's
    document is its owner's."""
    if document.lane is None:
        return False
    if rule.scope is RuleScope.USER and document.owner_id != rule.owner_id:
        return False
    drawer = await uow.drawers.get(document.drawer_id)
    return can_write_document(user, document, drawer)


async def _enqueue(uow: UnitOfWork, id: RuleApplicationId, now: datetime) -> None:
    payload: dict[str, JsonValue] = {"application_id": str(id)}
    await uow.jobs.enqueue(APPLY_JOB, payload, run_at=now, dedup_key=f"{APPLY_JOB}:{id}")
