"""The pipeline nodes `apply_rules` and `file`."""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime

from papiq.core.domain.classification import FieldCheck, field_key
from papiq.core.domain.documents import Document
from papiq.core.domain.fields import FieldDefinition
from papiq.core.domain.ids import FieldId
from papiq.core.domain.json_value import JsonValue
from papiq.core.domain.pipeline import RULES, NodeResult, Outcome
from papiq.core.domain.rule_engine import SPACE, Mode
from papiq.core.domain.rules import Trigger
from papiq.core.ports import (
    ObjectStore,
    PatternMatcher,
    TemplateRenderer,
    UnitOfWork,
    UnitOfWorkFactory,
)
from papiq.core.services._access import may_file
from papiq.core.services.rules.running import (
    Prepared,
    active_rules,
    apply_plan,
    checked_rules,
    prepare,
    provenance,
    run_rules,
)


class ApplyRulesNode:
    """Runs the rules with trigger `ingest`: the global ones and those of the document's owner.

    The text is loaded and its patterns run here, outside any transaction; the rules are
    evaluated and applied when the result is stored, on the state that is stored. Conflicts,
    refused actions and forced reviews make the node uncertain: the document waits in the
    inbox before it is filed. So does a required field set by hand that has no value after the
    rules (issue #52)."""

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        store: ObjectStore,
        matcher: PatternMatcher,
        templates: TemplateRenderer,
        *,
        max_text: int,
    ) -> None:
        self._uow = uow
        self._store = store
        self._matcher = matcher
        self._templates = templates
        self._max_text = max_text

    async def run(self, document: Document) -> "RulesResult":
        async with self._uow() as uow:
            owner = await uow.users.find(document.owner_id)
            rules = await active_rules(uow, owner, Trigger.INGEST)
        prepared = await prepare(
            self._store, self._matcher, rules, document, max_text=self._max_text
        )
        return RulesResult(prepared, self._matcher, self._templates)


@dataclass(frozen=True)
class RulesResult:
    prepared: Prepared
    matcher: PatternMatcher
    templates: TemplateRenderer

    async def apply(self, uow: UnitOfWork, document: Document, now: datetime) -> NodeResult:
        owner = await uow.users.get(document.owner_id)
        rules = await active_rules(uow, owner, Trigger.INGEST)
        input: dict[str, JsonValue] = {
            "trigger": Trigger.INGEST.value,
            "rules": checked_rules(rules),
        }
        definitions = {item.id: item for item in await uow.fields.list_all()}
        if not rules:
            ok = NodeResult(outcome=Outcome.OK, model_version=RULES, input=input)
            return _waiting_for_hand(document, definitions, ok)
        origin = provenance(await uow.processing_log.list_for(document.id), document)
        run = await run_rules(
            uow,
            self.matcher,
            self.templates,
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
            result = NodeResult(
                outcome=Outcome.UNCERTAIN,
                reason="; ".join(reasons),
                model_version=RULES,
                input=input,
                output=output,
            )
        else:
            result = NodeResult(outcome=Outcome.OK, model_version=RULES, input=input, output=output)
        return _waiting_for_hand(document, definitions, result)


def _waiting_for_hand(
    document: Document, definitions: Mapping[FieldId, FieldDefinition], result: NodeResult
) -> NodeResult:
    """`result`, uncertain with a check for each required field set by hand that neither a
    person nor a rule has given a value: the person enters it in the inbox."""
    waiting = [
        FieldCheck(
            field=field_key(definition.id),
            outcome=Outcome.UNCERTAIN,
            confidence=0,
            reason=f"'{definition.name}' is set by hand and has no value yet",
        )
        for definition in sorted(definitions.values(), key=lambda item: item.name)
        if not definition.is_read
        and definition.applies_to(document.document_type_id)
        and definition.is_required_for(document.document_type_id)
        and definition.id not in document.fields
    ]
    if not waiting:
        return result
    checks = result.output.get("fields")
    output = {
        **result.output,
        "fields": [*(checks if isinstance(checks, list) else []), *(c.to_json() for c in waiting)],
    }
    reasons = [*([result.reason] if result.reason else []), *(c.reason or "" for c in waiting)]
    return replace(result, outcome=Outcome.UNCERTAIN, reason="; ".join(reasons), output=output)


class FileNode:
    """Files the document into its space. The owner must still be allowed to write to it (a
    share can be withdrawn while the document is processed), unless an active admin put the
    document there; otherwise the node is uncertain and the owner chooses another space in the
    inbox."""

    async def run(self, document: Document) -> "FileResult":
        return FileResult()


@dataclass(frozen=True)
class FileResult:
    async def apply(self, uow: UnitOfWork, document: Document, now: datetime) -> NodeResult:
        space = await uow.spaces.get(document.space_id)
        if not await may_file(uow, document, space):
            reason = f"the owner may not file into space '{space.name}' (any more)"
            check = FieldCheck(field=SPACE, outcome=Outcome.UNCERTAIN, confidence=0, reason=reason)
            return NodeResult(
                outcome=Outcome.UNCERTAIN, reason=reason, output={"fields": [check.to_json()]}
            )
        return NodeResult(outcome=Outcome.OK)
