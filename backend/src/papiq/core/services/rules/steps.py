"""The pipeline steps `apply_rules` and `file`."""

from dataclasses import dataclass
from datetime import datetime

from papiq.core.domain.classification import FieldCheck
from papiq.core.domain.documents import Document
from papiq.core.domain.json_value import JsonValue
from papiq.core.domain.permissions import can_file_into
from papiq.core.domain.pipeline import Outcome, StepResult
from papiq.core.domain.rule_engine import DRAWER, Mode
from papiq.core.domain.rules import Trigger
from papiq.core.ports import ObjectStore, PatternMatcher, UnitOfWork, UnitOfWorkFactory
from papiq.core.services.inbox import RULES
from papiq.core.services.rules.running import (
    Prepared,
    active_rules,
    apply_plan,
    checked_rules,
    prepare,
    provenance,
    run_rules,
)


class ApplyRulesStep:
    """Runs the rules with trigger `ingest`: the global ones and those of the document's owner.

    The text is loaded and its patterns run here, outside any transaction; the rules are
    evaluated and applied when the result is stored, on the state that is stored. Conflicts,
    refused actions and forced reviews make the step uncertain: the document waits in the
    inbox before it is filed."""

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        store: ObjectStore,
        matcher: PatternMatcher,
        *,
        max_text: int,
    ) -> None:
        self._uow = uow
        self._store = store
        self._matcher = matcher
        self._max_text = max_text

    async def run(self, document: Document) -> "RulesResult":
        async with self._uow() as uow:
            owner = await uow.users.find(document.owner_id)
            rules = await active_rules(uow, owner, Trigger.INGEST)
        prepared = await prepare(
            self._store, self._matcher, rules, document, max_text=self._max_text
        )
        return RulesResult(prepared, self._matcher)


@dataclass(frozen=True)
class RulesResult:
    prepared: Prepared
    matcher: PatternMatcher

    async def apply(self, uow: UnitOfWork, document: Document, now: datetime) -> StepResult:
        owner = await uow.users.get(document.owner_id)
        rules = await active_rules(uow, owner, Trigger.INGEST)
        input: dict[str, JsonValue] = {
            "trigger": Trigger.INGEST.value,
            "rules": checked_rules(rules),
        }
        if not rules:
            return StepResult(outcome=Outcome.OK, model_version=RULES, input=input)
        origin = provenance(await uow.processing_log.list_for(document.id), document)
        definitions = {item.id: item for item in await uow.attributes.list_all()}
        run = await run_rules(
            uow,
            self.matcher,
            mode=Mode.INGEST,
            document=document,
            owner=owner,
            rules=rules,
            prepared=self.prepared,
            origin=origin,
            definitions=definitions,
            locked=origin.person,
        )
        output = run.output()
        problem = apply_plan(document, run.plan, definitions, now)
        reasons = run.plan.reasons + ([problem] if problem else [])
        if reasons:
            return StepResult(
                outcome=Outcome.UNCERTAIN,
                reason="; ".join(reasons),
                model_version=RULES,
                input=input,
                output=output,
            )
        return StepResult(outcome=Outcome.OK, model_version=RULES, input=input, output=output)


class FileStep:
    """Files the document into its drawer. The owner must still be allowed to write to it (a
    share can be withdrawn while the document is processed); otherwise the step is uncertain
    and the owner chooses another drawer in the inbox."""

    async def run(self, document: Document) -> "FileResult":
        return FileResult()


@dataclass(frozen=True)
class FileResult:
    async def apply(self, uow: UnitOfWork, document: Document, now: datetime) -> StepResult:
        owner = await uow.users.get(document.owner_id)
        drawer = await uow.drawers.get(document.drawer_id)
        if not can_file_into(owner, drawer):
            reason = f"the owner may not file into drawer '{drawer.name}' (any more)"
            check = FieldCheck(field=DRAWER, outcome=Outcome.UNCERTAIN, confidence=0, reason=reason)
            return StepResult(
                outcome=Outcome.UNCERTAIN, reason=reason, output={"fields": [check.to_json()]}
            )
        return StepResult(outcome=Outcome.OK)
