"""Rules with trigger `change`: when a person changes a document's metadata.

- Only on documents whose processing is complete; while processing runs (or waits in the
  inbox), the node `apply_rules` sees the change anyway.
- Edge, not state: a rule acts only if it holds after the change and did not hold before. A
  title change does not apply every rule again, and a new rule does not act silently on old
  documents.
- The person wins: fields they set in this change, or decided before in this processing run,
  are not changed by a rule (the rule is logged as overruled).
- Nothing turns yellow: conflicts, refused actions and forced reviews change nothing and are
  only reported (a filed document would disappear for everyone else).
- The change, the rules' change, the events and the log entry are stored together. The log
  entry also records what the person changed, for later rule runs.
"""

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import datetime

from papiq.core.domain.classification import TAGS, TITLE
from papiq.core.domain.documents import Document, DocumentChanges
from papiq.core.domain.fields import FieldDefinition
from papiq.core.domain.ids import FieldId
from papiq.core.domain.json_value import JsonValue
from papiq.core.domain.pipeline import (
    RULES_CHANGE,
    Node,
    NodeResult,
    NodeRun,
    Outcome,
    ProcessingStatus,
)
from papiq.core.domain.rule_engine import Mode, changed_fields
from papiq.core.domain.rules import Rule, Trigger
from papiq.core.domain.users import User
from papiq.core.ports import ObjectStore, PatternMatcher, TemplateRenderer, UnitOfWork
from papiq.core.services.rules.running import (
    Prepared,
    RuleRun,
    active_rules,
    apply_plan,
    checked_rules,
    corrected_fields,
    field_patterns,
    person_record,
    prepare,
    provenance,
    run_rules,
    space_choice,
)


class ChangeRules:
    """Runs the change rules for `DocumentService`."""

    def __init__(
        self,
        store: ObjectStore,
        matcher: PatternMatcher,
        templates: TemplateRenderer,
        *,
        max_text: int,
        pipeline_version: str,
    ) -> None:
        self._store = store
        self._matcher = matcher
        self._templates = templates
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

    def space_chosen(self, document: Document, *, actor: User, now: datetime) -> NodeRun:
        """The log entry of a person moving the document: rules leave the space then."""
        return space_choice(
            document,
            node=Node.APPLY_RULES,
            actor=actor.id,
            trigger="move",
            version=self._version,
            now=now,
        )

    async def after_change(
        self,
        uow: UnitOfWork,
        *,
        actor: User,
        before: Document,
        document: Document,
        changes: DocumentChanges,
        prepared: Prepared,
        definitions: Mapping[FieldId, FieldDefinition],
        now: datetime,
        log: bool = True,
    ) -> RuleRun | None:
        """After the person's change is applied to `document`: run the rules, apply their
        change and (with `log`) write the log entry. Returns the run, if rules were
        evaluated. `changes`: the person's change as given, before `checked_changes` (a topic
        it clears because the new contact does not enable it is no decision of theirs)."""
        changed = changed_fields(changes)
        record = person_record(
            changed=changed,
            tags_before=before.tag_ids,
            tags_after=document.tag_ids,
            corrected=corrected_fields(before, document, decided=changed),
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
                model_tags=frozenset() if TAGS in changed else origin.model_tags,
                machine_title=None if TITLE in changed else origin.machine_title,
                tags_added=(origin.tags_added & document.tag_ids)
                | (document.tag_ids - before.tag_ids),
                tags_removed=(origin.tags_removed - document.tag_ids)
                | (before.tag_ids - document.tag_ids),
            )
            before_patterns, _ = await field_patterns(self._matcher, rules, before)
            run = await run_rules(
                uow,
                self._matcher,
                self._templates,
                mode=Mode.CHANGE,
                document=document,
                owner=owner,
                rules=rules,
                prepared=prepared,
                origin=origin,
                definitions=definitions,
                before=(before, before_patterns),
                locked=origin.person,
            )
            input["rules"] = checked_rules(rules)
            output = run.output()
            reason = apply_plan(document, run.plan, definitions, now)
            if reason is not None:
                output["problem"] = reason
        if log:
            await uow.processing_log.append(
                NodeRun(
                    document_id=document.id,
                    node=Node.APPLY_RULES,
                    run=document.processing.run,
                    result=NodeResult(
                        outcome=Outcome.OK, model_version=RULES_CHANGE, input=input, output=output
                    ),
                    pipeline_version=self._version,
                    started_at=now,
                    duration=now - now,
                )
            )
        return run
