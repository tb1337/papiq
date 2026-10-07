"""The classification steps: classify (contact, type, tags, date) and extract attributes.

Each step asks the language model once, checks the answer's structure and asks once more if it
does not fit; a second unfit answer fails the step (red). Every proposed field is then checked
against facts (`FieldCheck`): the confidence comes from what the text shows and what the master
data holds, never from the model's own judgement. Fields that pass are applied to the document;
uncertain ones are only proposed, and the step is uncertain (the document waits in the inbox).
An unreachable model raises, so the pipeline retries the step.
"""

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from papiq.core.domain.attributes import (
    AttributeDefinition,
    AttributeType,
    AttributeValue,
    Money,
    Url,
)
from papiq.core.domain.classification import (
    CONTACT,
    DOCUMENT_DATE,
    DOCUMENT_TYPE,
    TAGS,
    FieldCheck,
    attribute_field,
    attribute_to_json,
    checks_to_json,
)
from papiq.core.domain.documents import UNSET, Document, DocumentChanges, Unset
from papiq.core.domain.errors import ValidationError
from papiq.core.domain.evidence import DocumentText
from papiq.core.domain.ids import AttributeId, ContactId
from papiq.core.domain.json_value import JsonObject, JsonValue
from papiq.core.domain.master_data import Contact, DocumentType, Tag
from papiq.core.domain.names import best_match, mentions, name_key
from papiq.core.domain.pipeline import Outcome, StepResult
from papiq.core.ports import Clock, LanguageModel, ObjectStore, UnitOfWorkFactory
from papiq.core.ports.llm import StructuredAnswer, StructuredRequest
from papiq.core.services.classification.answers import (
    AnswerError,
    ClassifyAnswer,
    Proposal,
    attribute_keys,
    classify_schema,
    extract_schema,
    parse_classify,
    parse_extract,
)
from papiq.core.services.classification.prompts import (
    CLASSIFY_PROMPT,
    CLASSIFY_SYSTEM,
    EXTRACT_PROMPT,
    EXTRACT_SYSTEM,
    Shortened,
    classify_message,
    extract_message,
    retry_message,
    shorten,
)
from papiq.core.services.objects import markdown_key
from papiq.core.services.pipeline import MetadataResult

_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_RAW_LENGTH = 4000  # characters of an unfit answer kept in the log
_AMBIGUITY = 0.05  # two contacts this close to each other are ambiguous
_EARLIEST = date(1900, 1, 1)
_LATEST_AHEAD = timedelta(days=366)

NO_MODEL = "no language model is configured (PAPIQ_LLM_BASE_URL)"


@dataclass(frozen=True, kw_only=True)
class ClassificationPolicy:
    """`accept`: a field with at least this confidence is applied; `suggest_contact`: an
    existing contact at least this similar is suggested. Texts longer than `input_budget`
    characters are shortened; at most `max_tags` tags are listed in a prompt."""

    accept: float = 0.9
    suggest_contact: float = 0.75
    input_budget: int = 12_000
    max_tags: int = 200


DEFAULT_POLICY = ClassificationPolicy()


class _UnfitAnswerError(Exception):
    """The model answered twice with something that does not fit the schema."""

    def __init__(self, errors: list[str], answers: list[StructuredAnswer]) -> None:
        super().__init__(errors[-1])
        self.errors = errors
        self.answers = answers


class _ModelStep:
    def __init__(
        self,
        uow: UnitOfWorkFactory,
        store: ObjectStore,
        model: LanguageModel | None,
        clock: Clock,
        policy: ClassificationPolicy = DEFAULT_POLICY,
    ) -> None:
        self._uow = uow
        self._store = store
        self._model = model
        self._clock = clock
        self._policy = policy

    async def _text(self, document: Document) -> str:
        return (await self._store.get(markdown_key(document.id))).decode("utf-8")

    async def _ask[T](
        self, system: str, message: str, schema: JsonObject, name: str, parse: Callable[[str], T]
    ) -> tuple[T, list[StructuredAnswer]]:
        """The parsed answer and all answers; once more after an unfit one."""
        assert self._model is not None
        answers: list[StructuredAnswer] = []
        errors: list[str] = []
        user = message
        for _ in range(2):
            answer = await self._model.complete(
                StructuredRequest(system=system, user=user, schema=schema, schema_name=name)
            )
            answers.append(answer)
            try:
                return parse(answer.content), answers
            except AnswerError as error:
                errors.append(str(error))
                user = retry_message(message, str(error))
        raise _UnfitAnswerError(errors, answers)

    def _input(self, document: Document, text: str, sent: Shortened, prompt: str) -> JsonObject:
        assert self._model is not None
        return {
            "markdown": markdown_key(document.id),
            "characters": len(text),
            "sent": len(sent.text),
            "truncated": sent.omitted > 0,
            "prompt": prompt,
            "model": self._model.model,
            "endpoint": self._model.endpoint,
        }

    def _unfit(self, error: _UnfitAnswerError, input: JsonObject) -> StepResult:
        return StepResult(
            outcome=Outcome.FAILED,
            reason=f"the model's answer does not fit the schema: {error}",
            model_version=_model_version(error.answers[-1]),
            input=input,
            output={
                "attempts": len(error.answers),
                "errors": list(error.errors),
                "answers": [answer.content[:_RAW_LENGTH] for answer in error.answers],
            },
        )


class ClassifyStep(_ModelStep):
    """Contact, document type, tags and document date."""

    async def run(self, document: Document) -> StepResult | MetadataResult:
        if self._model is None:
            return StepResult(outcome=Outcome.UNCERTAIN, reason=NO_MODEL)
        text = await self._text(document)
        async with self._uow() as uow:
            contacts = await uow.contacts.list_all()
            types = sorted(await uow.document_types.list_all(), key=lambda item: item.name)
            tags = sorted(await uow.tags.list_all(), key=lambda item: item.name)
        facts = DocumentText(text)
        listed = _listed_tags(tags, facts, self._policy.max_tags)
        sent = shorten(text, self._policy.input_budget)
        input = self._input(document, text, sent, CLASSIFY_PROMPT) | {
            "contacts": len(contacts),
            "document_types": len(types),
            "tags": len(tags),
            "tags_listed": len(listed),
        }
        type_names = [item.name for item in types]
        tag_names = [item.name for item in listed]
        try:
            answer, answers = await self._ask(
                CLASSIFY_SYSTEM,
                classify_message(type_names, tag_names, sent),
                classify_schema(type_names, tag_names),
                "classification",
                parse_classify,
            )
        except _UnfitAnswerError as error:
            return self._unfit(error, input)

        if (
            answer.contact.value is None
            and answer.document_type is None
            and answer.new_document_type is None
            and answer.document_date.value is None
        ):
            return StepResult(
                outcome=Outcome.FAILED,
                reason="nothing recognised: no contact, document type or date",
                model_version=_model_version(answers[-1]),
                input=input,
                output=_output(answer.raw, [], answers),
            )
        checks = [
            self._check_contact(answer.contact, contacts, facts),
            _check_type(answer, types),
            _check_tags(answer, tags),
            _check_date(DOCUMENT_DATE, answer.document_date, facts, self._today()),
        ]
        changes = DocumentChanges(
            contact_id=_applied(checks[0], lambda value: ContactId(UUID(value))),
            document_type_id=_applied(checks[1], lambda value: _by_id(types, value).id),
            tag_ids=frozenset(_by_id(tags, value).id for value in _list(checks[2].value)),
            document_date=_applied(checks[3], date.fromisoformat),
        )
        return MetadataResult(_result(checks, answer.raw, answers, input), changes)

    def _today(self) -> date:
        return self._clock.now().date()

    def _check_contact(
        self, proposal: Proposal, contacts: Sequence[Contact], facts: DocumentText
    ) -> FieldCheck:
        name = proposal.value
        common: dict[str, Any] = {
            "field": CONTACT,
            "proposed": name,
            "evidence": proposal.evidence,
        }
        if not isinstance(name, str):
            return FieldCheck(
                outcome=Outcome.UNCERTAIN, confidence=0, reason="no contact recognised", **common
            )
        match = best_match(name, [(contact, contact.name) for contact in contacts])
        text_key = name_key(facts.raw)
        named = mentions(text_key, name_key(name)) or (
            match.best is not None and mentions(text_key, name_key(match.best.name))
        )
        ambiguous = match.best is not None and match.runner_up >= match.score - _AMBIGUITY
        best = match.best
        if best is not None and match.score >= self._policy.suggest_contact:
            if match.score >= self._policy.accept and named and not ambiguous:
                return FieldCheck(
                    outcome=Outcome.OK, confidence=match.score, value=str(best.id), **common
                )
            if ambiguous:
                reason = f"several contacts are similar to '{name}'; the closest is '{best.name}'"
            elif not named:
                reason = f"'{name}' does not appear in the text"
            else:
                reason = f"'{name}' is similar to the contact '{best.name}'"
            capped = match.score if named and not ambiguous else min(match.score, 0.5)
            return FieldCheck(
                outcome=Outcome.UNCERTAIN,
                confidence=min(capped, self._policy.accept - 0.01),
                reason=reason,
                suggestion=str(best.id),
                **common,
            )
        return FieldCheck(
            outcome=Outcome.UNCERTAIN,
            confidence=match.score,
            reason=f"no contact matches '{name}'; a new contact is proposed",
            new_name=name,
            **common,
        )


class ExtractAttributesStep(_ModelStep):
    """The values of the attributes that apply to the document: those of its type and the
    global ones. A type's attribute that the document does not show is uncertain; a global
    one stays unset."""

    async def run(self, document: Document) -> StepResult | MetadataResult:
        async with self._uow() as uow:
            definitions = sorted(
                (
                    item
                    for item in await uow.attributes.list_all()
                    if item.applies_to(document.document_type_id)
                ),
                key=lambda item: item.name,
            )
            document_type = (
                None
                if document.document_type_id is None
                else await uow.document_types.find(document.document_type_id)
            )
        if not definitions:
            return StepResult(outcome=Outcome.OK, output={"fields": []})
        if self._model is None:
            return StepResult(outcome=Outcome.UNCERTAIN, reason=NO_MODEL)
        text = await self._text(document)
        facts = DocumentText(text)
        keys = attribute_keys(definitions)
        sent = shorten(text, self._policy.input_budget)
        input = self._input(document, text, sent, EXTRACT_PROMPT) | {
            "attributes": {key: str(definition.id) for key, definition in keys.items()},
        }
        try:
            proposals, answers = await self._ask(
                EXTRACT_SYSTEM,
                extract_message(None if document_type is None else document_type.name, keys, sent),
                extract_schema(keys),
                "attributes",
                lambda content: parse_extract(content, list(keys)),
            )
        except _UnfitAnswerError as error:
            return self._unfit(error, input)
        checks = [
            _check_attribute(definition, proposals[key], facts, self._clock.now().date())
            for key, definition in keys.items()
        ]
        values: dict[AttributeId, object] = {}
        for check, definition in zip(checks, definitions, strict=True):
            if check.ok and check.value is not None:
                values[definition.id] = _parse_value(definition, check.value)
        raw: JsonObject = {key: _proposal_json(proposal) for key, proposal in proposals.items()}
        return MetadataResult(
            _result(checks, {"attributes": raw}, answers, input),
            DocumentChanges(attributes=values),
        )


# --- checks -----------------------------------------------------------------------------------


def _check_type(answer: ClassifyAnswer, types: Sequence[DocumentType]) -> FieldCheck:
    proposed = answer.document_type
    common: dict[str, Any] = {"field": DOCUMENT_TYPE, "proposed": proposed}
    if proposed is not None:
        known = _by_name(types, proposed)
        if known is not None:
            return FieldCheck(outcome=Outcome.OK, confidence=1, value=str(known.id), **common)
        return FieldCheck(
            outcome=Outcome.UNCERTAIN,
            confidence=0,
            reason=f"'{proposed}' is not a document type; a new type is proposed",
            new_name=proposed,
            **common,
        )
    if answer.new_document_type is not None:
        return FieldCheck(
            outcome=Outcome.UNCERTAIN,
            confidence=0,
            reason=f"no document type fits; a new type '{answer.new_document_type}' is proposed",
            new_name=answer.new_document_type,
            **common,
        )
    return FieldCheck(
        outcome=Outcome.UNCERTAIN, confidence=0, reason="no document type recognised", **common
    )


def _check_tags(answer: ClassifyAnswer, tags: Sequence[Tag]) -> FieldCheck:
    """Known tags are applied; unknown and new ones are only proposals (admins create tags)."""
    known: list[str] = []
    proposed_new = list(answer.new_tags)
    for name in answer.tags:
        tag = _by_name(tags, name)
        if tag is None:
            proposed_new.append(name)
        elif str(tag.id) not in known:
            known.append(str(tag.id))
    return FieldCheck(
        field=TAGS,
        outcome=Outcome.OK,
        confidence=1,
        proposed={"tags": list(answer.tags), "new_tags": list(answer.new_tags)},
        value=list(known),
        suggestion=[name for name in dict.fromkeys(proposed_new)] or None,
    )


def _check_date(field: str, proposal: Proposal, facts: DocumentText, today: date) -> FieldCheck:
    common = _common(field, proposal)
    if proposal.value is None:
        return FieldCheck(
            outcome=Outcome.UNCERTAIN, confidence=0, reason="no date recognised", **common
        )
    value = _date(proposal.value)
    if value is None:
        return FieldCheck(
            outcome=Outcome.UNCERTAIN,
            confidence=0,
            reason=f"{proposal.value!r} is not a date (YYYY-MM-DD)",
            **common,
        )
    if not _EARLIEST <= value <= today + _LATEST_AHEAD:
        return FieldCheck(
            outcome=Outcome.UNCERTAIN, confidence=0, reason=f"{value} is not plausible", **common
        )
    return _verified(common, value.isoformat(), proposal, facts, facts.has_date(value), "date")


def _check_attribute(
    definition: AttributeDefinition, proposal: Proposal, facts: DocumentText, today: date
) -> FieldCheck:
    field = attribute_field(definition.id)
    common = _common(field, proposal)
    if proposal.value is None:
        if definition.is_global:
            return FieldCheck(outcome=Outcome.OK, confidence=1, **common)
        return FieldCheck(
            outcome=Outcome.UNCERTAIN,
            confidence=0,
            reason=f"'{definition.name}' was not found",
            **common,
        )
    if definition.data_type is AttributeType.DATE:
        check = _check_date(field, proposal, facts, today)
        if check.ok:
            return check
        reason = check.reason or "invalid"
        return FieldCheck(
            outcome=Outcome.UNCERTAIN,
            confidence=check.confidence,
            reason=f"'{definition.name}': {reason}",
            suggestion=check.suggestion,
            **common,
        )
    try:
        value = _parse_value(definition, proposal.value)
    except ValidationError:
        return FieldCheck(
            outcome=Outcome.UNCERTAIN,
            confidence=0,
            reason=f"{proposal.value!r} is not a valid value of '{definition.name}' "
            f"({definition.data_type})",
            **common,
        )
    shown, what = _shown(definition, value, proposal, facts)
    check = _verified(common, attribute_to_json(value), proposal, facts, shown, what)
    if check.ok:
        return check
    return FieldCheck(
        outcome=Outcome.UNCERTAIN,
        confidence=check.confidence,
        reason=f"'{definition.name}': {check.reason}",
        suggestion=check.suggestion,
        **common,
    )


def _shown(
    definition: AttributeDefinition, value: AttributeValue, proposal: Proposal, facts: DocumentText
) -> tuple[bool, str]:
    """Whether the text shows the value, and what was looked for. Yes/no and choice values
    are not written as such; for them the quoted passage must be in the text."""
    match value:
        case Money():
            if not facts.has_number(value.amount):
                return False, "amount"
            return facts.has_currency(value.currency), f"currency {value.currency}"
        case Decimal():
            return facts.has_number(value), "number"
        case Url():
            return facts.contains(value.value), "link"
        case str() if definition.data_type is AttributeType.TEXT:
            return facts.contains(value), "text"
        case _:
            return proposal.evidence is not None, "quoted passage"


def _common(field: str, proposal: Proposal) -> dict[str, Any]:
    return {"field": field, "proposed": proposal.value, "evidence": proposal.evidence}


def _verified(
    common: dict[str, Any],
    value: JsonValue,
    proposal: Proposal,
    facts: DocumentText,
    shown: bool,
    what: str,
) -> FieldCheck:
    if proposal.evidence is not None and not facts.contains(proposal.evidence):
        reason = "the quoted passage does not appear in the text"
    elif not shown:
        reason = f"the {what} does not appear in the text"
    else:
        return FieldCheck(outcome=Outcome.OK, confidence=1, value=value, **common)
    return FieldCheck(
        outcome=Outcome.UNCERTAIN,
        confidence=0,
        reason=reason,
        suggestion=value,
        **common,
    )


# --- values -----------------------------------------------------------------------------------


def _parse_value(definition: AttributeDefinition, raw: JsonValue) -> AttributeValue:
    """A proposed value read leniently (a number may come as number or with a decimal
    comma), then checked against the definition. ValidationError if it does not fit."""
    value: object = raw
    try:
        match definition.data_type:
            case AttributeType.NUMBER:
                value = _decimal(raw)
            case AttributeType.AMOUNT if isinstance(raw, dict):
                currency = raw.get("currency")
                value = Money(
                    _decimal(raw.get("amount")),
                    currency.strip().upper() if isinstance(currency, str) else "",
                )
            case AttributeType.DATE:
                value = _date(raw)
            case AttributeType.LINK if isinstance(raw, str):
                value = Url(raw.strip())
            case AttributeType.TEXT | AttributeType.CHOICE if isinstance(raw, str):
                value = raw.strip()
    except (InvalidOperation, ValueError, TypeError):
        raise ValidationError(f"invalid value {raw!r}") from None
    return definition.validate(value)


def _decimal(raw: JsonValue) -> Decimal:
    if isinstance(raw, bool) or not isinstance(raw, str | int | float):
        raise ValueError(raw)
    text = str(raw).strip().replace(" ", "")
    if "," in text and "." not in text:
        text = text.replace(",", ".")
    number = Decimal(text)
    if not number.is_finite():
        raise ValueError(raw)
    return number


def _date(raw: JsonValue) -> date | None:
    if not isinstance(raw, str) or not _ISO_DATE.fullmatch(raw.strip()):
        return None
    try:
        return date.fromisoformat(raw.strip())
    except ValueError:
        return None


# --- helpers ----------------------------------------------------------------------------------


def _listed_tags(tags: Sequence[Tag], facts: DocumentText, limit: int) -> list[Tag]:
    """All tags if they fit the limit; otherwise first those named in the text."""
    if len(tags) <= limit:
        return list(tags)
    text_key = name_key(facts.raw)
    named = [tag for tag in tags if mentions(text_key, name_key(tag.name))]
    rest = [tag for tag in tags if tag not in named]
    return sorted((named + rest)[:limit], key=lambda tag: tag.name)


def _by_name[T: Contact | DocumentType | Tag](items: Sequence[T], name: str) -> T | None:
    wanted = name.strip().casefold()
    return next((item for item in items if item.name.casefold() == wanted), None)


def _by_id[T: Contact | DocumentType | Tag](items: Sequence[T], id: JsonValue) -> T:
    return next(item for item in items if str(item.id) == id)


def _applied[T](check: FieldCheck, convert: Callable[[str], T]) -> T | Unset:
    if check.ok and isinstance(check.value, str):
        return convert(check.value)
    return UNSET


def _list(value: JsonValue) -> list[JsonValue]:
    return value if isinstance(value, list) else []


def _model_version(answer: StructuredAnswer) -> str:
    return f"{answer.model} ({answer.fingerprint})" if answer.fingerprint else answer.model


def _proposal_json(proposal: Proposal) -> JsonObject:
    return {"value": proposal.value, "evidence": proposal.evidence}


def _output(
    answer: JsonObject, checks: list[FieldCheck], answers: list[StructuredAnswer]
) -> JsonObject:
    tokens: list[JsonValue] = [
        {"prompt": item.prompt_tokens, "completion": item.completion_tokens} for item in answers
    ]
    return {
        "attempts": len(answers),
        "answer": answer,
        "fields": checks_to_json(checks),
        "tokens": tokens,
    }


def _result(
    checks: list[FieldCheck],
    answer: JsonObject,
    answers: list[StructuredAnswer],
    input: JsonObject,
) -> StepResult:
    uncertain = [check for check in checks if not check.ok]
    return StepResult(
        outcome=Outcome.UNCERTAIN if uncertain else Outcome.OK,
        reason="; ".join(f"{check.field}: {check.reason}" for check in uncertain) or None,
        confidence=min((check.confidence for check in checks), default=1.0),
        model_version=_model_version(answers[-1]),
        input=input,
        output=_output(answer, checks, answers),
    )
