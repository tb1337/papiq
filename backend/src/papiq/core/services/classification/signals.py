"""The signals: each proposes metadata from one source, for the judge (`domain.verdict`).

A signal never decides the lane. It ends OK with what it found, or, if it has nothing to say,
with `output.abstained` naming why (no model trained, no embeddings, ...). Only the language
model's signal fails, when its answers do not fit or it cannot be reached after every retry.

- `signal_facts`: contacts whose name or alias stands whole in the text, and identifiers of
  contacts (IBAN, VAT number, ...) found in it, with the topic each points to; evidence, not a
  proposal of its own. The codes on the archive's pages count as well (`domain.codes`): their
  identifiers like those of the text, the payee of a GiroCode like a name in the text.
- `signal_classifier`: the trained classifier's probabilities for contact, type, topic and
  tags.
- `signal_neighbours`: the most similar confirmed documents the owner may read, by vector,
  with their contact, type, topic and tags.
"""

import json
import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from papiq.core.domain.codes import (
    Code,
    code_identifiers,
    codes_from_json,
    codes_to_json,
)
from papiq.core.domain.documents import Document
from papiq.core.domain.errors import (
    ClassifierError,
    EmbeddingsError,
    NotFoundError,
    SearchError,
    ValidationError,
)
from papiq.core.domain.facts import facts_in
from papiq.core.domain.identifiers import IdentifierKind, identifiers_in
from papiq.core.domain.ids import ContactId, DocumentTypeId, TagId
from papiq.core.domain.json_value import JsonObject, JsonValue
from papiq.core.domain.master_data import Contact
from papiq.core.domain.permissions import reach_access
from papiq.core.domain.pipeline import SIGNALS, Node, NodeResult, NodeRun, Outcome
from papiq.core.domain.search import Visibility
from papiq.core.domain.verdict import (
    ClassifierView,
    FactsView,
    KnownLabels,
    ModelProposal,
    Neighbour,
    Signals,
)
from papiq.core.ports import (
    CodeReader,
    DocumentFilter,
    Embeddings,
    ObjectStore,
    UnitOfWork,
    UnitOfWorkFactory,
)
from papiq.core.ports.search_index import SearchIndex, SimilarQuery
from papiq.core.services.classifier import ClassifierService
from papiq.core.services.indexing import split_sections
from papiq.core.services.nodes import work_directory
from papiq.core.services.objects import archive_key, codes_key, load_text

log = logging.getLogger(__name__)

ABSTAINED = "abstained"
"""Key of the output of a signal that had nothing to say: why."""
FACTS = "facts"
_MOST = 20  # contacts and identifiers a signal reports
_RANKED = 5  # classes of contact and type the classifier's signal keeps


def abstained(reason: str, *, model_version: str | None = None) -> NodeResult:
    return NodeResult(outcome=Outcome.OK, model_version=model_version, output={ABSTAINED: reason})


async def _text(store: ObjectStore, document: Document) -> str | None:
    try:
        return (await load_text(store, document.id)).decode("utf-8", "replace")
    except NotFoundError:
        return None


class FactsSignal:
    """Hard evidence in the text and in the codes on the pages: a contact's name or alias,
    whole, and identifiers of contacts. A name alone proposes nothing (a bank statement names
    payees, a pay slip the employee); it backs or contradicts what other signals propose.

    The codes are read from the archive once (`codes`, without a reader none) and kept beside
    the document (`codes_key`); the OCR node drops them with a new archive. A code that cannot
    be read never fails the signal. The identifiers found are kept for the document
    (`DocumentIdentifierRepository`), applied with the result."""

    def __init__(
        self, uow: UnitOfWorkFactory, store: ObjectStore, codes: CodeReader | None = None
    ) -> None:
        self._uow = uow
        self._store = store
        self._codes = codes

    async def run(self, document: Document) -> "NodeResult | FactsResult":
        text = await _text(self._store, document)
        if text is None:
            return abstained("the document has no text", model_version=FACTS)
        codes = await self.codes(document)
        async with self._uow() as uow:
            contacts = await uow.contacts.list_all()
        output = facts_of(text, contacts, codes or ())
        if codes is not None:
            output["codes"] = len(codes)
        found = identifiers_in(text)
        for code in codes or ():
            found |= code_identifiers(code)
        result = NodeResult(outcome=Outcome.OK, model_version=FACTS, output=output)
        return FactsResult(result, frozenset(found))

    async def codes(self, document: Document) -> tuple[Code, ...] | None:
        """The codes on the archive's pages, read before or now; None without a reader, an
        archive, or when they cannot be read."""
        if self._codes is None:
            return None
        key = codes_key(document.id)
        try:
            kept = codes_from_json(json.loads(await self._store.get(key)))
        except NotFoundError:
            kept = None
        except (ValueError, ValidationError):
            log.warning("reading an unreadable file of codes again", extra={"key": key})
            kept = None
        if kept is not None:
            return kept
        try:
            async with work_directory() as work:
                archive = work / "archive.pdf"
                await self._store.download(archive_key(document.id), archive)
                reading = await self._codes.read_codes(archive)
        except NotFoundError:
            return None
        except Exception:
            log.warning(
                "the codes were not read", exc_info=True, extra={"document_id": str(document.id)}
            )
            return None
        data = json.dumps(codes_to_json(reading.codes), ensure_ascii=False).encode("utf-8")
        await self._store.put(key, data, content_type="application/json")
        return reading.codes


@dataclass(frozen=True)
class FactsResult:
    """The facts, and the identifiers the document shows: kept for it when the result is
    recorded."""

    result: NodeResult
    identifiers: frozenset[tuple[IdentifierKind, str]]

    @property
    def output(self) -> JsonObject:
        return self.result.output

    async def apply(self, uow: UnitOfWork, document: Document, now: datetime) -> NodeResult:
        await uow.document_identifiers.replace(document.id, self.identifiers)
        return self.result


def facts_of(text: str, contacts: Sequence[Contact], codes: Sequence[Code] = ()) -> JsonObject:
    """What `signal_facts` reports for `text` and the `codes` of its pages (`domain.facts`);
    what a code holds is marked `in_code`."""
    facts = facts_in(text, contacts, codes)

    def marked(item: JsonObject, in_code: bool) -> JsonObject:
        return {**item, "in_code": True} if in_code else item

    return {
        "named": [
            marked(
                {"contact_id": str(item.contact_id), "name": item.name, "alias": item.alias},
                item.in_code,
            )
            for item in facts.named
        ],
        "identifiers": [
            marked(
                {
                    "contact_id": str(item.contact_id),
                    "kind": item.kind.value,
                    "value": item.value,
                    **({} if item.topic_id is None else {"topic_id": str(item.topic_id)}),
                },
                item.in_code,
            )
            for item in facts.identifiers
            if item.contact_id is not None
        ],
        "unknown_identifiers": [
            marked({"kind": item.kind.value, "value": item.value}, item.in_code)
            for item in facts.identifiers
            if item.contact_id is None
        ],
    }


class ClassifierSignal:
    """The trained classifier's probabilities, and the classes it knows (a class it does not
    know it cannot name: the judge then goes by the other signals)."""

    def __init__(self, store: ObjectStore, classifier: ClassifierService | None) -> None:
        self._store = store
        self._classifier = classifier

    async def run(self, document: Document) -> NodeResult:
        if self._classifier is None:
            return abstained("no classifier is configured")
        text = await _text(self._store, document)
        if text is None:
            return abstained("the document has no text")
        try:
            predicted = await self._classifier.predict(text)
        except ClassifierError as error:
            log.warning("the classifier could not be asked: %s", error)
            return abstained(f"the classifier cannot be used: {error}")
        if predicted is None:
            return abstained("no model is trained yet")
        model, prediction = predicted
        output: JsonObject = {
            "model": model.number,
            "contacts": [[str(id), round(p, 4)] for id, p in prediction.contacts[:_RANKED]],
            "document_types": [
                [str(id), round(p, 4)] for id, p in prediction.document_types[:_RANKED]
            ],
            "topics": [[str(id), round(p, 4)] for id, p in prediction.topics[:_RANKED]],
            "tags": {str(id): round(p, 4) for id, p in prediction.tags.items()},
            "known": {
                "contacts": _texts(prediction.known_contacts),
                "document_types": _texts(prediction.known_types),
                "topics": _texts(prediction.known_topics),
            },
        }
        return NodeResult(
            outcome=Outcome.OK,
            model_version=f"classifier {model.number}",
            output=output,
        )


@dataclass(frozen=True)
class NeighboursPolicy:
    count: int = 5  # neighbours asked for; 0: none
    chunk_size: int = 1500  # of the first section, as the index cuts it
    document_prefix: str = ""  # put before it, as the index does
    dimensions: int | None = None  # the length of the vectors the index takes


class NeighboursSignal:
    """The most similar confirmed documents the owner may read: by the vector of the start of
    the text, against the index's vectors; their contact, type, topic and tags (from the
    database, not the index, which lags a little), and what any confirmed document carries
    (`known`): the values similar documents could have named."""

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        store: ObjectStore,
        index: SearchIndex | None,
        embeddings: Embeddings | None,
        policy: NeighboursPolicy | None = None,
    ) -> None:
        self._uow = uow
        self._store = store
        self._index = index
        self._embeddings = embeddings
        self._policy = policy or NeighboursPolicy()

    async def run(self, document: Document) -> NodeResult:
        if self._policy.count == 0:
            return abstained("neighbours are turned off")
        if self._index is None or self._embeddings is None:
            return abstained("there is no search index with embeddings")
        text = await _text(self._store, document)
        sections = [] if text is None else split_sections(text, self._policy.chunk_size, 1)
        if not sections:
            return abstained("the document has no text")
        try:
            embedded = await self._embeddings.embed([self._policy.document_prefix + sections[0]])
        except EmbeddingsError as error:
            return abstained(f"the text could not be embedded: {error}")
        vector = tuple(embedded.vectors[0]) if embedded.vectors else ()
        wanted = self._policy.dimensions
        if not vector or (wanted is not None and len(vector) != wanted):
            return abstained("the text's vector does not fit the index")
        async with self._uow() as uow:
            owner = await uow.users.get(document.owner_id)
            spaces = await uow.spaces.list_accessible(owner.id)
        visibility = Visibility(owner.id, frozenset(space.id for space in spaces))
        try:
            hits = await self._index.similar(
                SimilarQuery(
                    vector=vector,
                    visibility=visibility,
                    filter=DocumentFilter(confirmed=True),
                    exclude=document.id,
                    limit=self._policy.count,
                )
            )
        except SearchError as error:
            return abstained(f"the search index cannot be asked: {error}")
        neighbours: list[JsonValue] = []
        async with self._uow() as uow:
            known = await uow.documents.confirmed_labels(owner.id)
            # They vote on the tags and topics the classification sets only, not on those set
            # by hand.
            tags = await uow.tags.list_all()
            targets = {tag.id for tag in tags if tag.is_target}
            topics = {tag.id for tag in tags if tag.is_topic_target}
            for hit in hits:
                found = await uow.documents.find(hit.id)
                if found is None or found.confirmed_at is None:
                    continue  # the index is behind
                space = await uow.spaces.find(found.space_id)
                if space is None or reach_access(owner, found, space) is None:
                    continue
                neighbours.append(
                    {
                        "document_id": str(found.id),
                        "score": None if hit.score is None else round(hit.score, 4),
                        "contact_id": None if found.contact_id is None else str(found.contact_id),
                        "document_type_id": (
                            None if found.document_type_id is None else str(found.document_type_id)
                        ),
                        "tag_ids": _texts(found.tag_ids & targets),
                        "topic_id": (str(found.topic_id) if found.topic_id in topics else None),
                    }
                )
        if not neighbours:
            return abstained("no similar confirmed document")
        output: JsonObject = {
            "neighbours": neighbours,
            "known": {
                "contacts": _texts(known.contacts),
                "document_types": _texts(known.document_types),
                "tags": _texts(known.tags & targets),
                "topics": _texts(known.topics & topics),
            },
        }
        return NodeResult(outcome=Outcome.OK, output=output)


def _texts(ids: Iterable[object]) -> list[JsonValue]:
    return list[JsonValue](sorted(str(id) for id in ids))


# --- what the judge reads ---------------------------------------------------------------------


def latest_signals(log: Sequence[NodeRun]) -> dict[Node, NodeRun]:
    """The latest entry of each signal: a reprocessing from the judge on keeps the signals of
    an earlier run."""
    latest: dict[Node, NodeRun] = {}
    for entry in log:
        if entry.node in SIGNALS and entry.result.outcome is Outcome.OK:
            latest[entry.node] = entry
    return latest


def signals_of(entries: Mapping[Node, NodeRun]) -> Signals:
    """The signals as the judge's rules take them; a signal that abstained (or never ran) is
    None."""

    def output(node: Node) -> Mapping[str, Any] | None:
        entry = entries.get(node)
        if entry is None or ABSTAINED in entry.result.output:
            return None
        return entry.result.output

    neighbours = output(Node.SIGNAL_NEIGHBOURS)
    return Signals(
        model=_model_view(output(Node.SIGNAL_LLM)),
        classifier=_classifier_view(output(Node.SIGNAL_CLASSIFIER)),
        neighbours=_neighbours_view(neighbours),
        facts=_facts_view(output(Node.SIGNAL_FACTS)),
        neighbours_know=_known_view(neighbours),
    )


def model_proposal(entries: Mapping[Node, NodeRun]) -> Mapping[str, Any] | None:
    """The language model's proposal as it stored it; None if it gave none."""
    entry = entries.get(Node.SIGNAL_LLM)
    if entry is None or ABSTAINED in entry.result.output:
        return None
    proposal = entry.result.output.get("proposal")
    return proposal if isinstance(proposal, dict) else None


def _model_view(output: Mapping[str, Any] | None) -> ModelProposal | None:
    proposal = None if output is None else output.get("proposal")
    if not isinstance(proposal, dict):
        return None
    sender = proposal.get("sender") or {}
    return ModelProposal(
        sender=_optional_text(sender.get("value")),
        sender_quote=_optional_text(sender.get("evidence")),
        recipient=_optional_text(proposal.get("recipient")),
        document_type=_optional_text(proposal.get("document_type")),
        new_document_type=_optional_text(proposal.get("new_document_type")),
        tags=tuple(item for item in proposal.get("tags") or () if isinstance(item, str)),
        new_tags=tuple(item for item in proposal.get("new_tags") or () if isinstance(item, str)),
        topic=_optional_text(proposal.get("topic")),
    )


def _classifier_view(output: Mapping[str, Any] | None) -> ClassifierView | None:
    if output is None:
        return None
    known = output.get("known") or {}
    return ClassifierView(
        contacts=[(ContactId(UUID(id)), float(p)) for id, p in output.get("contacts") or ()],
        document_types=[
            (DocumentTypeId(UUID(id)), float(p)) for id, p in output.get("document_types") or ()
        ],
        tags={TagId(UUID(id)): float(p) for id, p in (output.get("tags") or {}).items()},
        known_contacts=frozenset(ContactId(UUID(id)) for id in known.get("contacts") or ()),
        known_types=frozenset(DocumentTypeId(UUID(id)) for id in known.get("document_types") or ()),
        topics=[(TagId(UUID(id)), float(p)) for id, p in output.get("topics") or ()],
        known_topics=frozenset(TagId(UUID(id)) for id in known.get("topics") or ()),
    )


def _neighbours_view(output: Mapping[str, Any] | None) -> tuple[Neighbour, ...] | None:
    if output is None:
        return None
    return tuple(
        Neighbour(
            document=str(item["document_id"]),
            contact=None if item.get("contact_id") is None else ContactId(UUID(item["contact_id"])),
            document_type=(
                None
                if item.get("document_type_id") is None
                else DocumentTypeId(UUID(item["document_type_id"]))
            ),
            tags=frozenset(TagId(UUID(tag)) for tag in item.get("tag_ids") or ()),
            similarity=float(item["score"]) if isinstance(item.get("score"), int | float) else None,
            topic=None if item.get("topic_id") is None else TagId(UUID(item["topic_id"])),
        )
        for item in output.get("neighbours") or ()
    )


def _known_view(output: Mapping[str, Any] | None) -> KnownLabels | None:
    """What the similar documents could have named; None for an entry from before it was
    kept (then they count as covering every value)."""
    known = None if output is None else output.get("known")
    if not isinstance(known, dict):
        return None
    return KnownLabels(
        contacts=frozenset(ContactId(UUID(id)) for id in known.get("contacts") or ()),
        document_types=frozenset(
            DocumentTypeId(UUID(id)) for id in known.get("document_types") or ()
        ),
        tags=frozenset(TagId(UUID(id)) for id in known.get("tags") or ()),
        topics=frozenset(TagId(UUID(id)) for id in known.get("topics") or ()),
    )


def _facts_view(output: Mapping[str, Any] | None) -> FactsView | None:
    if output is None:
        return None
    identifiers: dict[ContactId, tuple[str, ...]] = {}
    topics: dict[ContactId, dict[TagId, tuple[str, ...]]] = {}
    for item in output.get("identifiers") or ():
        contact, kind = ContactId(UUID(item["contact_id"])), str(item["kind"])
        identifiers[contact] = (*identifiers.get(contact, ()), kind)
        if item.get("topic_id") is not None:
            pairs = topics.setdefault(contact, {})
            topic = TagId(UUID(item["topic_id"]))
            pairs[topic] = (*pairs.get(topic, ()), kind)
    return FactsView(
        named=frozenset(ContactId(UUID(item["contact_id"])) for item in output.get("named") or ()),
        identifiers=identifiers,
        topics=topics,
    )


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None
