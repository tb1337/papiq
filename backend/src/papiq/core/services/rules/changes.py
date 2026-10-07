"""Rules with trigger `change`: when a person changes a document's metadata.

- Only on documents whose processing is complete; while processing runs (or waits in the
  inbox), the step `apply_rules` sees the change anyway.
- Edge, not state: a rule acts only if it holds after the change and did not hold before. A
  title change does not apply every rule again, and a new rule does not act silently on old
  documents.
- The person wins: fields they set in this change are not changed by a rule (the rule is
  logged as overruled).
- Nothing turns yellow: conflicts, refused actions and forced reviews change nothing and are
  only reported (a filed document would disappear for everyone else).
- The change, the rules' change, the events and the log entry are stored together. The log
  entry also records what the person changed, for later rule runs.
"""

import copy
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import datetime

from papiq.core.domain.attributes import AttributeDefinition
from papiq.core.domain.documents import Document, DocumentChanges
from papiq.core.domain.ids import AttributeId
from papiq.core.domain.json_value import JsonValue
from papiq.core.domain.pipeline import Outcome, ProcessingStatus, Step, StepResult, StepRun
from papiq.core.domain.rule_engine import Mode, changed_fields
from papiq.core.domain.rules import Rule, Trigger
from papiq.core.domain.users import User
from papiq.core.ports import ObjectStore, PatternMatcher, UnitOfWork
from papiq.core.services.inbox import RULES_CHANGE
from papiq.core.services.rules.running import (
    Prepared,
    RuleRun,
    active_rules,
    apply_plan,
    attribute_patterns,
    checked_rules,
    person_record,
    prepare,
    provenance,
    run_rules,
)


class ChangeRules:
    """Runs the change rules for `DocumentService`."""

    def __init__(
        self,
        store: ObjectStore,
        matcher: PatternMatcher,
        *,
        max_text: int,
        pipeline_version: str,
    ) -> None:
        self._store = store
        self._matcher = matcher
        self._max_text = max_text
        self._version = pipeline_version

    async def rules(self, uow: UnitOfWork, document: Document) -> list[Rule]:
        """The rules a change of `document` may set off."""
        if document.processing.status is not ProcessingStatus.COMPLETED:
            return []
        owner = await uow.users.find(document.owner_id)
        return await active_rules(uow, owner, Trigger.CHANGE)

    async def prepare(self, rules: Sequence[Rule], document: Document) -> Prepared:
        """Outside a transaction: the text and its patterns, if a rule needs them."""
        return await prepare(self._store, self._matcher, rules, document, max_text=self._max_text)

    def snapshot(self, document: Document) -> Document:
        """The state before the change, to compare against."""
        return copy.deepcopy(document)

    async def after_change(
        self,
        uow: UnitOfWork,
        *,
        actor: User,
        before: Document,
        document: Document,
        changes: DocumentChanges,
        prepared: Prepared,
        definitions: Mapping[AttributeId, AttributeDefinition],
        now: datetime,
        log: bool = True,
    ) -> RuleRun | None:
        """After the person's change is applied to `document`: run the rules, apply their
        change and (with `log`) write the log entry. Returns the run, if rules were
        evaluated."""
        changed = changed_fields(changes)
        record = person_record(
            changed=changed, tags_before=before.tag_ids, tags_after=document.tag_ids
        )
        input: dict[str, JsonValue] = {
            "trigger": Trigger.CHANGE.value,
            "actor": str(actor.id),
            **record,
        }
        rules = await self.rules(uow, document)
        run: RuleRun | None = None
        output: dict[str, JsonValue] = {}
        reason: str | None = None
        if rules:
            owner = await uow.users.get(document.owner_id)
            origin = provenance(await uow.processing_log.list_for(document.id), document)
            origin = replace(
                origin,
                person=origin.person | changed,
                model_values={
                    name: value
                    for name, value in origin.model_values.items()
                    if name not in changed
                },
                tags_added=(origin.tags_added - before.tag_ids)
                | (document.tag_ids - before.tag_ids),
                tags_removed=(origin.tags_removed - document.tag_ids)
                | (before.tag_ids - document.tag_ids),
            )
            before_patterns, _ = await attribute_patterns(self._matcher, rules, before)
            run = await run_rules(
                uow,
                self._matcher,
                mode=Mode.CHANGE,
                document=document,
                owner=owner,
                rules=rules,
                prepared=prepared,
                origin=origin,
                definitions=definitions,
                before=(before, before_patterns),
                locked=changed,
            )
            input["rules"] = checked_rules(rules)
            output = run.output()
            reason = apply_plan(document, run.plan, definitions, now)
            if reason is not None:
                output["problem"] = reason
        if log:
            await uow.processing_log.append(
                StepRun(
                    document_id=document.id,
                    step=Step.APPLY_RULES,
                    run=document.processing.run,
                    result=StepResult(
                        outcome=Outcome.OK, model_version=RULES_CHANGE, input=input, output=output
                    ),
                    pipeline_version=self._version,
                    started_at=now,
                    duration=now - now,
                )
            )
        return run
