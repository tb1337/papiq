"""The nodes that understand a document: the language model's signal, the judge and the
fields node.

A node that asks the language model checks the answer's structure and asks once more if it
does not fit; a second unfit answer fails the node (red). An unreachable model raises, so the
pipeline retries the node. Every value is then checked against facts (`FieldCheck`): the
confidence comes from what the text shows, what the master data holds and what the other
signals say, never from the model's own judgement. Values that pass are applied to the
document; uncertain ones are only proposed, and the node is uncertain (the document waits in
the inbox).
"""

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from papiq.core.domain.classification import (
    DOCUMENT_DATE,
    TITLE,
    FieldCheck,
    checks_to_json,
    field_id_of,
    field_key,
)
from papiq.core.domain.documents import UNSET, Document, DocumentChanges, Unset
from papiq.core.domain.errors import (
    NotFoundError,
    UnprocessableDocumentError,
    ValidationError,
)
from papiq.core.domain.evidence import (
    DocumentText,
    read_amount,
    read_date,
    read_number,
    read_period,
)
from papiq.core.domain.exchanges import ModelCall
from papiq.core.domain.fields import (
    FieldDefinition,
    FieldType,
    FieldValue,
    Money,
    Url,
    field_from_json,
    field_to_json,
)
from papiq.core.domain.ids import ContactId, FieldId
from papiq.core.domain.json_value import JsonObject, JsonValue
from papiq.core.domain.master_data import Contact, DocumentType, Tag
from papiq.core.domain.names import mentions, name_key, named_share
from papiq.core.domain.periods import Period, PeriodKind
from papiq.core.domain.pipeline import Node, NodeResult, NodeRun, Outcome
from papiq.core.domain.reading import PageReading, readings_from_json
from papiq.core.domain.verdict import (
    DEFAULT_THRESHOLDS,
    Thresholds,
    judge_contact,
    judge_tags,
    judge_topic,
    judge_type,
)
from papiq.core.ports import Clock, LanguageModel, ObjectStore, TextLocator, UnitOfWorkFactory
from papiq.core.ports.llm import StructuredAnswer, StructuredRequest
from papiq.core.services.classification.answers import (
    AnswerError,
    Proposal,
    extract_schema,
    field_keys,
    parse_extract,
    parse_propose,
    propose_schema,
)
from papiq.core.services.classification.prompts import (
    EXTRACT_PROMPT,
    EXTRACT_SYSTEM,
    PROPOSE_PROMPT,
    PROPOSE_SYSTEM,
    Listed,
    Shortened,
    extract_message,
    propose_message,
    retry_message,
    shorten,
)
from papiq.core.services.classification.signals import (
    ABSTAINED,
    abstained,
    latest_signals,
    model_proposal,
    signals_of,
)
from papiq.core.services.exchanges import keep_calls
from papiq.core.services.nodes import work_directory
from papiq.core.services.objects import archive_key, load_text, text_key
from papiq.core.services.pipeline import MetadataResult
from papiq.core.services.titles import has_machine_title

log = logging.getLogger(__name__)

_RAW_LENGTH = 4000  # characters of an unfit answer kept in the log
_EARLIEST = date(1900, 1, 1)
_LATEST_AHEAD = timedelta(days=366)

NO_MODEL = "no language model is configured (PAPIQ_LLM_BASE_URL)"
JUDGE = "judge"
"""`model_version` of the judge's entry when the language model gave nothing."""


@dataclass(frozen=True, kw_only=True)
class ClassificationPolicy:
    """`accept`: a field with at least this confidence is applied; `suggest_contact`: an
    existing contact at least this similar is suggested. Texts longer than `input_budget`
    characters are shortened; at most `max_tags` tags and `max_contacts` contacts (those the
    text names most) are listed in a prompt. `default_currency`: the currency of an amount in
    a text that shows none. `poor_reading`: the share of unsure words from which a recognised
    page counts as poorly read (`PageReading.poor`)."""

    accept: float = 0.9
    suggest_contact: float = 0.75
    input_budget: int = 12_000
    max_tags: int = 200
    max_contacts: int = 20
    default_currency: str = "EUR"
    poor_reading: float = 0.08


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
        *,
        locator: TextLocator | None = None,
    ) -> None:
        """`locator`: find each quote on the pages of the archive PDF (`FieldCheck.locations`)."""
        self._uow = uow
        self._store = store
        self._model = model
        self._clock = clock
        self._policy = policy
        self._locator = locator

    async def _text(self, document: Document) -> str:
        return (await load_text(self._store, document.id)).decode("utf-8")

    async def _ask[T](
        self,
        document: Document,
        node: Node,
        system: str,
        message: str,
        schema: JsonObject,
        name: str,
        parse: Callable[[str], T],
    ) -> tuple[T, list[StructuredAnswer]]:
        """The parsed answer and all answers; once more after an unfit one. What was sent and
        received is kept beside the document (`exchanges_key`), also when the node fails."""
        assert self._model is not None
        answers: list[StructuredAnswer] = []
        calls: list[ModelCall] = []
        errors: list[str] = []
        user = message
        try:
            for _ in range(2):
                answer = await self._model.complete(
                    StructuredRequest(system=system, user=user, schema=schema, schema_name=name)
                )
                answers.append(answer)
                calls.append(
                    ModelCall(
                        system=system,
                        user=user,
                        answer=answer.content,
                        model=answer.model,
                        usage=answer.usage,
                    )
                )
                try:
                    return parse(answer.content), answers
                except AnswerError as error:
                    errors.append(str(error))
                    user = retry_message(message, str(error))
            raise _UnfitAnswerError(errors, answers)
        finally:
            if calls:
                await keep_calls(self._store, document, node, calls)

    async def _locate(self, document: Document, checks: list[FieldCheck]) -> list[FieldCheck]:
        """Each quote's page and boxes in the archive PDF. Without a locator, an archive or a
        readable PDF the checks stay as they are: the places help a person find the passage,
        they are not part of the check."""
        quotes = sorted({(check.evidence or "").strip() for check in checks} - {""})
        if self._locator is None or not quotes:
            return checks
        async with work_directory() as work:
            archive = work / "archive.pdf"
            try:
                await self._store.download(archive_key(document.id), archive)
                places = await self._locator.locate(archive, quotes)
            except Exception as error:
                log.warning(
                    "the quotes were not located: %s",
                    error,
                    exc_info=not isinstance(error, (NotFoundError, UnprocessableDocumentError)),
                    extra={"document_id": str(document.id)},
                )
                return checks
        found = dict(zip(quotes, places, strict=True))
        return [
            replace(check, locations=found.get((check.evidence or "").strip(), ()))
            for check in checks
        ]

    def _input(self, document: Document, text: str, sent: Shortened, prompt: str) -> JsonObject:
        assert self._model is not None
        return {
            "text": text_key(document.id),
            "characters": len(text),
            "sent": len(sent.text),
            "truncated": sent.omitted > 0,
            "prompt": prompt,
            "model": self._model.model,
            "endpoint": self._model.endpoint,
        }

    def _unfit(self, error: _UnfitAnswerError, input: JsonObject) -> NodeResult:
        return NodeResult(
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


class LlmSignal(_ModelStep):
    """The language model's proposal (`signal_llm`): one request for the sender and the
    recipient, the type, the topic (among those of the contacts listed), tags, date, a title
    and the language, and the values of the fields of the type it chooses and of the global
    ones. Without a model it abstains; two unfit answers fail it. It checks nothing: the judge
    and the fields node do."""

    async def run(self, document: Document) -> NodeResult:
        if self._model is None:
            return abstained(NO_MODEL)
        text = await self._text(document)
        async with self._uow() as uow:
            contacts = await uow.contacts.list_all()
            types = sorted(await uow.document_types.list_all(), key=lambda item: item.name)
            every = await uow.tags.list_all()
            # Only the tags the classification sets: document tags, not those set by hand.
            tags = sorted((item for item in every if item.is_target), key=lambda item: item.name)
            topics = {item.id: item.name for item in every if item.is_topic_target}
            definitions = sorted(
                (item for item in await uow.fields.list_all() if item.is_read),
                key=lambda item: item.name,
            )
        facts = DocumentText(text)
        listed = _listed_tags(tags, facts, self._policy.max_tags)
        candidates = _listed_contacts(contacts, facts, self._policy.max_contacts)
        # The topics of each contact listed: the answer names one of the sender's.
        enabled = {
            contact.id: tuple(
                sorted((topics[id] for id in contact.topic_ids if id in topics), key=str.casefold)
            )
            for contact in candidates
        }
        topic_names = sorted({name for names in enabled.values() for name in names})
        keys = field_keys(definitions)
        key_of = {definition.id: key for key, definition in keys.items()}
        typed = [
            (
                Listed(kind.name, description=kind.description),
                [
                    (key_of[definition.id], definition)
                    for definition in definitions
                    if not definition.is_global and definition.applies_to(kind.id)
                ],
            )
            for kind in types
        ]
        general = [(key_of[item.id], item) for item in definitions if item.is_global]
        sent = shorten(text, self._policy.input_budget)
        input = self._input(document, text, sent, PROPOSE_PROMPT) | {
            "contacts": len(contacts),
            "contacts_listed": len(candidates),
            "document_types": len(types),
            "tags": len(tags),
            "tags_listed": len(listed),
            "topics_listed": len(topic_names),
            "fields": {key: str(definition.id) for key, definition in keys.items()},
        }
        tag_names = [item.name for item in listed]
        try:
            answer, answers = await self._ask(
                document,
                Node.SIGNAL_LLM,
                PROPOSE_SYSTEM,
                propose_message(
                    [
                        Listed(item.name, aliases=tuple(item.aliases), topics=enabled[item.id])
                        for item in candidates
                    ],
                    typed,
                    general,
                    tag_names,
                    sent,
                ),
                propose_schema([item.name for item in types], tag_names, list(keys), topic_names),
                "proposal",
                lambda content: parse_propose(content, list(keys)),
            )
        except _UnfitAnswerError as error:
            return self._unfit(error, input)
        proposal: JsonObject = {
            "sender": _proposal_json(answer.sender),
            "recipient": answer.recipient,
            "document_type": answer.document_type,
            "new_document_type": answer.new_document_type,
            "topic": answer.topic,
            "tags": list[JsonValue](answer.tags),
            "new_tags": list[JsonValue](answer.new_tags),
            "document_date": _proposal_json(answer.document_date),
            "title": answer.title,
            "language": answer.language,
            "fields": {
                str(keys[key].id): _proposal_json(value) for key, value in answer.fields.items()
            },
        }
        return NodeResult(
            outcome=Outcome.OK,
            model_version=_model_version(answers[-1]),
            input=input,
            output={
                "attempts": len(answers),
                "answer": answer.raw,
                "tokens": _tokens(answers),
                "proposal": proposal,
            },
        )


class JudgeNode(_ModelStep):
    """The judge (`judge`): contact, document type, topic and tags from the signals' proposals
    by the rules of `domain.verdict`, and the document date the model read, checked against the
    text. What is green is applied; tags are added. A document the model answered for, but no
    signal recognised anything in, fails (red); without the model's proposal the judge is
    uncertain."""

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        store: ObjectStore,
        clock: Clock,
        policy: ClassificationPolicy = DEFAULT_POLICY,
        *,
        locator: TextLocator | None = None,
        classifier_threshold: float = DEFAULT_THRESHOLDS.classifier,
    ) -> None:
        super().__init__(uow, store, None, clock, policy, locator=locator)
        self._thresholds = Thresholds(
            accept=policy.accept,
            suggest=policy.suggest_contact,
            classifier=classifier_threshold,
        )

    async def run(self, document: Document) -> NodeResult | MetadataResult:
        async with self._uow() as uow:
            log = await uow.processing_log.list_for(document.id)
            contacts = await uow.contacts.list_all()
            types = await uow.document_types.list_all()
            tags = await uow.tags.list_all()
        entries = latest_signals(log)
        signals = signals_of(entries)
        proposal = model_proposal(entries)
        text = await self._text(document)
        facts = DocumentText(text)
        if proposal is None:
            silent = entries.get(Node.SIGNAL_LLM)
            why = (
                str(silent.result.output.get(ABSTAINED, "it did not run"))
                if silent is not None
                else "it did not run"
            )
            date_check = FieldCheck(
                field=DOCUMENT_DATE,
                outcome=Outcome.UNCERTAIN,
                confidence=0,
                reason=f"no date recognised: the language model gave none ({why})",
            )
        else:
            written = proposal.get("document_date") or {}
            date_check = _check_date(
                DOCUMENT_DATE,
                Proposal(value=written.get("value"), evidence=written.get("evidence")),
                facts,
                self._today(),
            )
        contact = judge_contact(signals, contacts, self._thresholds)
        kind = judge_type(signals, types, self._thresholds)
        topic = judge_topic(signals, contact, contacts, tags, self._thresholds)
        tagged = judge_tags(signals, tags, self._thresholds)
        model_entry = entries.get(Node.SIGNAL_LLM)
        llm_input = model_entry.result.input if model_entry is not None else {}
        input: JsonObject = {
            key: llm_input[key] for key in ("truncated", "model", "endpoint") if key in llm_input
        }
        input["signals"] = {
            node.value: (
                "abstained" if ABSTAINED in entry.result.output else entry.result.model_version
            )
            for node, entry in sorted(entries.items())
        }
        model_version = (
            model_entry.result.model_version
            if model_entry is not None and proposal is not None
            else JUDGE
        )
        recognised = contact.candidates or kind.candidates or date_check.value
        if proposal is not None and not (recognised or date_check.suggestion):
            # The model answered, and no signal recognised anything: as before the signals.
            # Without its proposal the judge is uncertain, and a person classifies.
            return NodeResult(
                outcome=Outcome.FAILED,
                reason="nothing recognised: no contact, document type or date",
                model_version=model_version,
                input=input,
                output={"fields": checks_to_json([contact, kind, topic, tagged, date_check])},
            )
        contact, kind, topic, tagged, date_check = await self._locate(
            document, [contact, kind, topic, tagged, date_check]
        )
        date_check = _readable(date_check, _ocr_readings(log), self._policy.poor_reading)
        checks = [contact, kind, topic, tagged, date_check]
        # A topic set is one of the contact's; a contact set that does not enable the
        # document's topic clears it (`checked_changes`).
        changes = DocumentChanges(
            contact_id=_applied(contact, lambda value: ContactId(UUID(value))),
            document_type_id=_applied(kind, lambda value: _by_id(types, value).id),
            topic_id=_applied(topic, lambda value: _by_id(tags, value).id),
            document_date=_applied(date_check, date.fromisoformat),
        )
        # Tags are added: those the document has (set by its owner) stay.
        add_tags = frozenset(_by_id(tags, value).id for value in _list(tagged.value))
        uncertain = [check for check in checks if not check.ok]
        result = NodeResult(
            outcome=Outcome.UNCERTAIN if uncertain else Outcome.OK,
            reason="; ".join(_reason(check) for check in uncertain) or None,
            confidence=min(check.confidence for check in checks),
            model_version=model_version,
            input=input,
            output={"fields": checks_to_json(checks)},
        )
        return MetadataResult(result, changes, add_tags)

    def _today(self) -> date:
        return self._clock.now().date()


class FieldsNode(_ModelStep):
    """The fields that apply to the document (those of its type and the global ones) and its
    title.

    The language model's signal gave the values of the global fields and of the fields of the
    type it chose; if the judge decided on that type, they are taken over, else (or for a
    document from before the signals) the fields it did not give are asked for. A required
    field the document does not show is uncertain, an optional one stays empty; a value given
    must pass its check either way. Without a model nothing reads the missing ones: the same
    holds, the required ones say why. A field set by hand is never asked for and keeps its
    value (the rules node checks that a required one has one).

    The title comes from the type's title pattern, or from the model's proposal, as long as
    the document's title is still the file name or the one set here before."""

    async def run(self, document: Document) -> NodeResult | MetadataResult:
        async with self._uow() as uow:
            log = await uow.processing_log.list_for(document.id)
            definitions = sorted(
                (
                    item
                    for item in await uow.fields.list_all()
                    if item.applies_to(document.document_type_id)
                ),
                key=lambda item: item.name,
            )
            document_type = (
                None
                if document.document_type_id is None
                else await uow.document_types.find(document.document_type_id)
            )
        entries = latest_signals(log)
        proposal = model_proposal(entries)
        given: dict[FieldId, Proposal] = {}
        if proposal is not None:
            same_type = document_type is not None and _same_name(
                proposal.get("document_type"), document_type.name
            )
            proposed = proposal.get("fields") or {}
            for definition in definitions:
                if definition.is_read and (definition.is_global or same_type):
                    written = proposed.get(str(definition.id)) or {}
                    given[definition.id] = Proposal(
                        value=written.get("value"), evidence=written.get("evidence")
                    )
        missing = [item for item in definitions if item.is_read and item.id not in given]
        answers: list[StructuredAnswer] = []
        text = await self._text(document) if any(item.is_read for item in definitions) else ""
        taken_over = len(given)
        input: JsonObject = {"taken_over": taken_over}
        unread: set[FieldId] = set()
        if missing and self._model is None:
            # Nothing read them: an optional field stays empty, a required one is open.
            unread = {item.id for item in missing}
            given |= {item.id: Proposal(value=None, evidence=None) for item in missing}
        elif missing:
            keys = field_keys(missing)
            sent = shorten(text, self._policy.input_budget)
            input |= self._input(document, text, sent, EXTRACT_PROMPT) | {
                "fields": {key: str(definition.id) for key, definition in keys.items()},
            }
            try:
                proposals, answers = await self._ask(
                    document,
                    Node.FIELDS,
                    EXTRACT_SYSTEM,
                    extract_message(
                        None if document_type is None else document_type.name, keys, sent
                    ),
                    extract_schema(keys),
                    "fields",
                    lambda content: parse_extract(content, list(keys)),
                )
            except _UnfitAnswerError as error:
                return self._unfit(error, input)
            given |= {keys[key].id: value for key, value in proposals.items()}
        facts = DocumentText(text)
        language = _text_value(proposal.get("language")) if proposal else None
        checks = [
            _check_field(
                definition,
                given[definition.id],
                facts,
                self._clock.now().date(),
                document.document_date,
                required=definition.is_required_for(document.document_type_id),
                language=language,
                default_currency=self._policy.default_currency,
            )
            if definition.is_read
            else _set_by_hand(definition)
            for definition in definitions
        ]
        checks = [
            replace(check, reason=f"'{definition.name}' was not read: {NO_MODEL}")
            if definition.id in unread and not check.ok
            else check
            for check, definition in zip(checks, definitions, strict=True)
        ]
        checks = await self._locate(document, checks)
        readings = _ocr_readings(log)
        checks = [
            _readable(check, readings, self._policy.poor_reading, definition.name)
            if definition.data_type in _LITERAL
            else check
            for check, definition in zip(checks, definitions, strict=True)
        ]
        values: dict[FieldId, object] = {}
        for check, definition in zip(checks, definitions, strict=True):
            if check.ok and check.value is not None:
                values[definition.id] = field_from_json(definition, check.value)
        title = _title(document, proposal, log)
        changes = DocumentChanges(fields=values)
        if title is not None and title.value is not None:
            changes = replace(changes, title=str(title.value))
        raw: JsonObject = {str(id): _proposal_json(value) for id, value in given.items()}
        all_checks = [*checks, *([title] if title is not None else [])]
        model_version = (
            _model_version(answers[-1])
            if answers
            else (entries[Node.SIGNAL_LLM].result.model_version if taken_over else None)
        )
        uncertain = [check for check in checks if not check.ok]
        result = NodeResult(
            outcome=Outcome.UNCERTAIN if uncertain else Outcome.OK,
            reason="; ".join(_reason(check) for check in uncertain) or None,
            confidence=min((check.confidence for check in checks), default=1.0),
            model_version=model_version,
            input=input,
            output={
                "attempts": len(answers),
                "answer": {"fields": raw},
                "fields": checks_to_json(all_checks),
                "tokens": _tokens(answers),
            },
        )
        return MetadataResult(result, changes)


def _title(
    document: Document, proposal: Mapping[str, Any] | None, log: Sequence[NodeRun]
) -> FieldCheck | None:
    """The title check: the model's title; None if the document has a title of its own (a
    person's or a rule's), or the model gave none. A type's title pattern is a rule
    (`rules.title_rule`): it replaces this title when the rules run."""
    if not has_machine_title(document, log):
        return None
    proposed = _text_value(proposal.get("title")) if proposal else None
    if not proposed:
        return None
    return FieldCheck(
        field=TITLE, outcome=Outcome.OK, confidence=1, proposed=proposed, value=proposed
    )


def _text_value(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _same_name(proposed: object, name: str) -> bool:
    return isinstance(proposed, str) and proposed.casefold() == name.casefold()


# --- checks -----------------------------------------------------------------------------------


def _check_date(field: str, proposal: Proposal, facts: DocumentText, today: date) -> FieldCheck:
    common = _common(field, proposal)
    if proposal.value is None:
        return FieldCheck(
            outcome=Outcome.UNCERTAIN, confidence=0, reason="no date recognised", **common
        )
    value = read_date(proposal.value, facts) if isinstance(proposal.value, str) else None
    if value is None:
        return FieldCheck(
            outcome=Outcome.UNCERTAIN,
            confidence=0,
            reason=f"{proposal.value!r} is not a date",
            **common,
        )
    common["quote_shows_value"] = _quoted(proposal, lambda quote: quote.has_date(value))
    if not _EARLIEST <= value <= today + _LATEST_AHEAD:
        return FieldCheck(
            outcome=Outcome.UNCERTAIN, confidence=0, reason=f"{value} is not plausible", **common
        )
    return _verified(
        common, value.isoformat(), proposal, facts, facts.has_date(value), "date", literal=True
    )


def _set_by_hand(definition: FieldDefinition) -> FieldCheck:
    """A field set by hand is never read: its value stays as the document has it. Whether a
    required one has a value is checked after the rules, which may set it (`ApplyRulesNode`)."""
    return FieldCheck(
        field=field_key(definition.id),
        outcome=Outcome.OK,
        confidence=1,
        reason=f"'{definition.name}' is set by hand",
    )


def _check_field(
    definition: FieldDefinition,
    proposal: Proposal,
    facts: DocumentText,
    today: date,
    document_date: date | None,
    *,
    required: bool,
    language: str | None = None,
    default_currency: str | None = None,
) -> FieldCheck:
    """A required field must be found, an optional one may stay empty; a value given must
    pass the checks either way. A date equal to the document date is only suggested: models
    put the document date into date fields the text does not show (a due date "within 30
    days")."""
    field = field_key(definition.id)
    common = _common(field, proposal)
    if proposal.value is None:
        if not required:
            return FieldCheck(outcome=Outcome.OK, confidence=1, **common)
        return FieldCheck(
            outcome=Outcome.UNCERTAIN,
            confidence=0,
            reason=f"'{definition.name}' was not found",
            **common,
        )
    if definition.data_type is FieldType.PERIOD:
        return _check_period(definition, proposal, facts, today, document_date)
    if definition.data_type is FieldType.DATE:
        check = _check_date(field, proposal, facts, today)
        if check.ok and document_date is not None and check.value == document_date.isoformat():
            check = replace(
                check,
                outcome=Outcome.UNCERTAIN,
                confidence=0,
                reason="the same date as the document date",
                suggestion=check.value,
                value=None,
            )
        if check.ok:
            return check
        reason = check.reason or "invalid"
        return FieldCheck(
            outcome=Outcome.UNCERTAIN,
            confidence=check.confidence,
            reason=f"'{definition.name}': {reason}",
            suggestion=check.suggestion,
            quote_shows_value=check.quote_shows_value,
            **common,
        )
    try:
        value = _parse_value(definition, proposal.value, facts, language, default_currency)
    except ValidationError:
        return FieldCheck(
            outcome=Outcome.UNCERTAIN,
            confidence=0,
            reason=f"{proposal.value!r} is not a valid value of '{definition.name}' "
            f"({definition.data_type})",
            **common,
        )
    common["quote_shows_value"] = _quoted(proposal, lambda quote: _literally(value, quote))
    shown, what = _shown(definition, value, proposal, facts, default_currency)
    literal = definition.data_type in _LITERAL
    check = _verified(common, field_to_json(value), proposal, facts, shown, what, literal=literal)
    if check.ok:
        return check
    return FieldCheck(
        outcome=Outcome.UNCERTAIN,
        confidence=check.confidence,
        reason=f"'{definition.name}': {check.reason}",
        suggestion=check.suggestion,
        **common,
    )


_SHORTEST_EVIDENCE = 4  # characters of a quoted passage that alone backs a yes/no or choice
# Values written as such: their quoted passage must show them (`_verified`), and a poorly
# read page does not back them (`_readable`).
_LITERAL = frozenset(
    {
        FieldType.TEXT,
        FieldType.NUMBER,
        FieldType.AMOUNT,
        FieldType.DATE,
        FieldType.LINK,
        FieldType.PERIOD,
    }
)


def _check_period(
    definition: FieldDefinition,
    proposal: Proposal,
    facts: DocumentText,
    today: date,
    document_date: date | None,
) -> FieldCheck:
    """A period as the model gives it: `{start, end}` (extract-4) or as written (propose-3:
    `September 2026`, `KW 38/2026`, `01.09.-30.09.2026`). Its quoted passage must show it. One
    without a year takes that of the document date and is only suggested, as is a single day
    equal to the document date (models put the document date where the text shows none)."""
    common = _common(field_key(definition.id), proposal)
    read = _read_period(proposal.value, document_date)
    if read is None:
        return FieldCheck(
            outcome=Outcome.UNCERTAIN,
            confidence=0,
            reason=f"'{definition.name}': {proposal.value!r} is not a period",
            **common,
        )
    period, yearless = read
    evidence = (proposal.evidence or "").strip()
    shows: bool | None = None
    if evidence:
        quote = DocumentText(evidence)
        shows = quote.has_period(period)
        if not shows and document_date is not None and quote.has_period(period, document_date):
            shows, yearless = True, True
    common["quote_shows_value"] = shows
    if not (period.start >= _EARLIEST and period.end <= today + _LATEST_AHEAD):
        return FieldCheck(
            outcome=Outcome.UNCERTAIN,
            confidence=0,
            reason=f"'{definition.name}': {period} is not plausible",
            **common,
        )
    in_text = facts.has_period(period, document_date if yearless else None)
    check = _verified(
        common, field_to_json(period), proposal, facts, in_text, "period", literal=True
    )
    if check.ok and yearless:
        reason = "no year given; the year of the document date is assumed"
    elif check.ok and period.kind is PeriodKind.DAY and period.start == document_date:
        reason = "the same day as the document date"
    elif check.ok:
        return check
    else:
        reason = check.reason or "invalid"
    return replace(
        check,
        outcome=Outcome.UNCERTAIN,
        confidence=0,
        reason=f"'{definition.name}': {reason}",
        suggestion=check.value if check.ok else check.suggestion,
        value=None,
    )


def _read_period(raw: JsonValue, document_date: date | None) -> tuple[Period, bool] | None:
    """The period of a proposed value, and whether its year was assumed."""
    if isinstance(raw, dict) and set(raw) == {"start", "end"}:
        start, end = (
            read_date(day) if isinstance(day, str) else None for day in (raw["start"], raw["end"])
        )
        if start is None or end is None or end < start:
            return None
        return Period(start, end), False
    return read_period(raw, document_date) if isinstance(raw, str) else None


def _shown(
    definition: FieldDefinition,
    value: FieldValue,
    proposal: Proposal,
    facts: DocumentText,
    default_currency: str | None,
) -> tuple[bool, str]:
    """Whether the text shows the value, and what was looked for. Yes/no and choice values
    are not written as such; for them a quoted passage of some length must be in the text. An
    amount in a text that shows no currency at all (a pay slip's bare numbers) is in the
    default currency."""
    match value:
        case Money():
            if not facts.has_number(value.amount):
                return False, "amount"
            if not facts.currencies and default_currency is not None:
                return value.currency == default_currency, f"currency {value.currency}"
            return facts.has_currency(value.currency), f"currency {value.currency}"
        case Decimal():
            return facts.has_number(value), "number"
        case Url():
            return facts.contains(value.value), "link"
        case str() if definition.data_type is FieldType.TEXT:
            return facts.contains(value), "text"
        case _:
            evidence = (proposal.evidence or "").strip()
            return len(evidence) >= _SHORTEST_EVIDENCE, "quoted passage"


def _quoted(proposal: Proposal, shows: Callable[[DocumentText], bool]) -> bool | None:
    """Whether the quoted passage itself shows the value; None without a quote."""
    evidence = (proposal.evidence or "").strip()
    return shows(DocumentText(evidence)) if evidence else None


def _literally(value: FieldValue, quote: DocumentText) -> bool:
    """Whether `quote` shows the value itself, not only what it follows from: a yes or no is
    never written as such."""
    match value:
        case Money():
            return quote.has_amount(value.amount)
        case Decimal():
            return quote.has_number(value)
        case Url():
            return quote.contains(value.value)
        case str():
            return quote.contains(value)
        case _:
            return False


def _ocr_readings(log: Sequence[NodeRun]) -> tuple[PageReading, ...]:
    """How well the OCR read the pages, by its latest entry; none for a document recognised
    before readings, or with text of its own."""
    for entry in reversed(log):
        if entry.node is Node.OCR:
            return readings_from_json(entry.result.output.get("readings"))
    return ()


def _readable(
    check: FieldCheck,
    readings: Sequence[PageReading],
    threshold: float,
    name: str | None = None,
) -> FieldCheck:
    """A checked value on a poorly read page is only suggested: on a broken scan a number
    may be anything, however sure the engine was of it. The page is that of its quote, or any
    recognised page if the quote was not found on one."""
    if not check.ok or check.value is None:
        return check
    pages = {location.page for location in check.locations}
    poor = next(
        (item for item in readings if item.poor(threshold) and (not pages or item.page in pages)),
        None,
    )
    if poor is None:
        return check
    reason = f"page {poor.page} was poorly read ({poor.unsure_share:.0%} of its words unsure)"
    return replace(
        check,
        outcome=Outcome.UNCERTAIN,
        confidence=0,
        reason=reason if name is None else f"'{name}': {reason}",
        suggestion=check.value,
        value=None,
    )


def _common(field: str, proposal: Proposal) -> dict[str, Any]:
    return {"field": field, "proposed": proposal.value, "evidence": proposal.evidence}


def _verified(
    common: dict[str, Any],
    value: JsonValue,
    proposal: Proposal,
    facts: DocumentText,
    shown: bool,
    what: str,
    *,
    literal: bool,
) -> FieldCheck:
    """The check of a value read from the text: the quoted passage must appear in it, and the
    text must show the value (`shown`). A `literal` value (an amount, number, date, text or
    link) also needs a quoted passage that shows it: elsewhere in the text the same digits
    may stand for anything, on a broken scan a lone fragment (`620` for 620,00 €)."""
    if proposal.evidence is not None and not facts.contains(proposal.evidence):
        reason = "the quoted passage does not appear in the text"
    elif literal and common["quote_shows_value"] is None:
        reason = "no quoted passage"
    elif literal and not common["quote_shows_value"]:
        reason = "the quoted passage does not show the value"
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


_YES_NO = {"true": True, "yes": True, "ja": True, "false": False, "no": False, "nein": False}


def _parse_value(
    definition: FieldDefinition,
    raw: JsonValue,
    facts: DocumentText,
    language: str | None,
    default_currency: str | None = None,
) -> FieldValue:
    """A proposed value read leniently, as a person writes it (`2.111,68 €`, `12.03.2026`,
    `ja`; an ambiguous number the way the text shows it, else as the document's `language`
    writes it), then checked against the definition. ValidationError if it does not fit."""
    value: object = raw
    match definition.data_type:
        case FieldType.NUMBER:
            value = _number(raw, facts, language)
        case FieldType.AMOUNT if isinstance(raw, dict):
            number, currency = _number(raw.get("amount"), facts, language), raw.get("currency")
            if number is not None:
                code = currency.strip().upper() if isinstance(currency, str) else ""
                value = Money(number, code)
        case FieldType.AMOUNT if isinstance(raw, str):
            amount = read_amount(raw, facts, language=language, default=default_currency)
            value = None if amount is None else Money(*amount)
        case FieldType.DATE if isinstance(raw, str):
            value = read_date(raw, facts)
        case FieldType.BOOLEAN if isinstance(raw, str):
            value = _YES_NO.get(raw.strip().casefold(), raw)
        case FieldType.LINK if isinstance(raw, str):
            value = Url(raw.strip())
        case FieldType.TEXT | FieldType.CHOICE if isinstance(raw, str):
            value = raw.strip()
    return definition.validate(value)


def _number(raw: JsonValue, facts: DocumentText, language: str | None) -> Decimal | None:
    """A number given as JSON number or as written."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int | float):
        number = Decimal(str(raw))
        return number if number.is_finite() else None
    return read_number(raw, facts, language=language) if isinstance(raw, str) else None


# --- helpers ----------------------------------------------------------------------------------


def _listed_tags(tags: Sequence[Tag], facts: DocumentText, limit: int) -> list[Tag]:
    """All tags if they fit the limit; otherwise first those named in the text."""
    if len(tags) <= limit:
        return list(tags)
    text_key = name_key(facts.raw)
    named = [tag for tag in tags if mentions(text_key, name_key(tag.name))]
    rest = [tag for tag in tags if tag not in named]
    return sorted((named + rest)[:limit], key=lambda tag: tag.name)


def _listed_contacts(contacts: Sequence[Contact], facts: DocumentText, limit: int) -> list[Contact]:
    """The contacts whose names or aliases the text shows most (see `named_share`), at most
    `limit`; none the text does not show at all."""
    text_key = name_key(facts.raw)
    words = frozenset(text_key.split())
    scored = [
        (max(named_share(text_key, words, name_key(own)) for own in contact.names()), contact)
        for contact in contacts
    ]
    ranked = sorted(
        ((score, contact) for score, contact in scored if score > 0),
        key=lambda entry: (-entry[0], entry[1].name.casefold()),
    )
    return [contact for _, contact in ranked[:limit]]


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


def _tokens(answers: Sequence[StructuredAnswer]) -> list[JsonValue]:
    return [
        {"prompt": item.prompt_tokens, "completion": item.completion_tokens, "usage": item.usage}
        for item in answers
    ]


def _reason(check: FieldCheck) -> str:
    """Reasons of field checks name the field already."""
    return str(check.reason) if field_id_of(check.field) else f"{check.field}: {check.reason}"
