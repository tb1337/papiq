"""The evaluation set on disk: master data, a training corpus, and per case a document as
Markdown with what is expected of it and, for runs without a model, a fixed answer.

Layout of a set directory:

- `master_data.json`: `contacts`, `document_types`, `tags` (names) and `fields`
  (`name`, `data_type`, `document_types` (names, or null for global), `choices`, `required`:
  true for a global field, else the names of the types it is required for); optional
  `topics` (names, or `name` and `parent`, a parent before its children), `contact_topics`
  (the topics by contact name), `contact_aliases` (aliases by contact name),
  `contact_identifiers` (by contact name, a list of `kind`, `value` and optionally `topic`,
  entered by hand), `type_descriptions` and `title_patterns` (by type name).
- `training/<name>.md` and `training/<name>.json`: the corpus the trained run takes over
  before the cases, a document's text and its metadata (`contact`, `document_type`, `tags`,
  `document_date`, `topic`: set by a person after the takeover, as Paperless has none).
- `cases/<name>.md`: the document text, as the parse node would produce it.
- `cases/<name>.json`: `description`, `expected` (`lane`, `contact`, `document_type`,
  `topic`, `tags`, `document_date`, `fields` by name, in the JSON form of the API) and `fake`
  (`classification`: `contact`, `document_type`, `new_document_type`, `tags`, `new_tags`,
  `document_date`, optionally `recipient`, `topic`, `title` and `language`, from which the
  answer of the language model's signal is built; `fields`: proposals by field name, in that
  answer as text; `lane`: the lane this answer leads to, if not the expected one). `expected`
  and `fake` may give `lanes` by run where a run differs; `runs` names the runs a case belongs
  to (default: both).
"""

import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from papiq.core.domain.fields import FieldType
from papiq.core.domain.json_value import JsonObject, JsonValue
from papiq.core.domain.master_data import Assignment
from papiq.core.domain.pipeline import Lane
from papiq.core.ports import StructuredRequest


class EvaluationSetError(Exception):
    """The evaluation set is incomplete or malformed."""


class Run(StrEnum):
    """The two runs of the set: without what the signals learn (a fresh installation), and
    after the training corpus was taken over, indexed and trained on."""

    COLD = "cold"
    TRAINED = "trained"


@dataclass(frozen=True)
class FieldSpec:
    """`required`: a global field required everywhere; `required_types`: the types a typed
    one is required for (in the JSON, `"required"` is either); `description`: what belongs in
    it, for the language model; `assignment`: `manual` for a field set by hand only."""

    name: str
    data_type: FieldType
    document_types: tuple[str, ...] | None
    choices: tuple[str, ...] = ()
    required: bool = False
    required_types: tuple[str, ...] = ()
    description: str | None = None
    assignment: Assignment = Assignment.AUTO


@dataclass(frozen=True)
class IdentifierSpec:
    kind: str
    value: str
    topic: str | None = None


@dataclass(frozen=True)
class MasterDataSpec:
    """`topics`: each with its parent (a parent before its children)."""

    contacts: tuple[str, ...]
    document_types: tuple[str, ...]
    tags: tuple[str, ...]
    fields: tuple[FieldSpec, ...]
    contact_aliases: dict[str, tuple[str, ...]] = field(default_factory=dict)
    contact_identifiers: dict[str, tuple[IdentifierSpec, ...]] = field(default_factory=dict)
    type_descriptions: dict[str, str] = field(default_factory=dict)
    title_patterns: dict[str, str] = field(default_factory=dict)
    topics: tuple[tuple[str, str | None], ...] = ()
    contact_topics: dict[str, tuple[str, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class Expected:
    lane: Lane
    contact: str | None
    document_type: str | None
    tags: frozenset[str]
    document_date: str | None
    fields: dict[str, JsonValue] = field(default_factory=dict)
    lanes: dict[Run, Lane] = field(default_factory=dict)
    topic: str | None = None

    def lane_in(self, run: Run) -> Lane:
        return self.lanes.get(run, self.lane)


@dataclass(frozen=True)
class FakeAnswer:
    classification: JsonObject
    fields: dict[str, JsonValue]
    lane: Lane
    lanes: dict[Run, Lane] = field(default_factory=dict)

    def lane_in(self, run: Run) -> Lane:
        return self.lanes.get(run, self.lane)

    def respond(self, request: StructuredRequest) -> JsonObject:
        """The answer to a request of the language model's signal (`proposal`) or of the fields
        node."""
        if request.schema_name == "proposal":
            return self._proposal(request.user)
        keys = dict(_KEY_LINE.findall(_before_document(request.user)))
        empty: JsonObject = {"value": None, "evidence": None}
        return {"fields": {key: self.fields.get(name, empty) for key, name in keys.items()}}

    def _proposal(self, message: str) -> JsonObject:
        """The classification as the signal's answer, with `contact` as the sender and
        everything else as it is (an answer that obeys instructions in the text keeps its
        extra keys): the fields it lists for the chosen type and for every document, those of
        `fields` with a value, written as text."""
        answer = dict(self.classification)
        if "contact" in answer:
            answer["sender"] = answer.pop("contact")
        keys = _proposal_keys(message, answer.get("document_type"))
        fields: list[JsonValue] = []
        for key, name in keys.items():
            proposal = self.fields.get(name)
            if isinstance(proposal, dict) and proposal.get("value") is not None:
                value = _written(proposal["value"])
                fields.append({"key": key, "value": value, "evidence": proposal.get("evidence")})
        return {
            "recipient": None,
            "title": None,
            "language": "de",
            "topic": None,
            **answer,
            "fields": fields,
        }


@dataclass(frozen=True)
class Case:
    name: str
    description: str
    text: str
    expected: Expected
    fake: FakeAnswer | None
    injection: bool = False
    runs: frozenset[Run] = frozenset(Run)


@dataclass(frozen=True)
class TrainingDocument:
    """A document of the training corpus: taken over with its metadata, as from Paperless."""

    name: str
    text: str
    contact: str | None
    document_type: str | None
    tags: frozenset[str]
    document_date: str | None
    topic: str | None = None


@dataclass(frozen=True)
class EvaluationSet:
    path: Path
    master_data: MasterDataSpec
    cases: tuple[Case, ...]
    training: tuple[TrainingDocument, ...] = ()

    def cases_in(self, run: Run) -> tuple[Case, ...]:
        return tuple(case for case in self.cases if run in case.runs)


def load_set(path: Path) -> EvaluationSet:
    """Read and check the set in `path`. EvaluationSetError names what is wrong."""
    master = _master_data(_json(path / "master_data.json"))
    directory = path / "cases"
    if not directory.is_dir():
        raise EvaluationSetError(f"{directory}: no cases directory")
    cases = tuple(_case(markdown, master) for markdown in sorted(directory.glob("*.md")))
    if not cases:
        raise EvaluationSetError(f"{directory}: no cases")
    corpus = path / "training"
    training = tuple(_training(markdown, master) for markdown in sorted(corpus.glob("*.md")))
    return EvaluationSet(path, master, cases, training)


def _before_document(message: str) -> str:
    return message.split("The document follows", 1)[0]


_KEY_LINE = re.compile(r'^\s*- (a\d+): "([^"\n]*)"', re.M)


def _proposal_keys(message: str, document_type: JsonValue) -> dict[str, str]:
    """Field names by key in the signal's request: those listed under `document_type` and
    those for every document."""
    keys: dict[str, str] = {}
    section, chosen = None, False
    for line in _before_document(message).splitlines():
        if not line.startswith(" ") and line.endswith(":"):
            section = line
            continue
        if section and section.startswith("Document types") and line.startswith("- "):
            chosen = isinstance(document_type, str) and (
                line == f"- {document_type}" or line.startswith(f"- {document_type}: ")
            )
            continue
        found = _KEY_LINE.match(line)
        if found and (chosen or (section or "").startswith("Fields for every document")):
            keys[found[1]] = found[2]
    return keys


def _written(value: JsonValue) -> JsonValue:
    """A value in the JSON form of the API as the document writes it."""
    match value:
        case {"amount": amount, "currency": currency}:
            return f"{amount} {currency}"
        case {"start": start, "end": end}:
            return f"{start} bis {end}"
        case bool():
            return "ja" if value else "nein"
        case int() | float():
            return str(value)
        case _:
            return value


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise EvaluationSetError(f"{path}: missing") from None
    except ValueError as error:
        raise EvaluationSetError(f"{path}: not JSON: {error}") from None


def _master_data(data: Any) -> MasterDataSpec:
    try:
        fields = tuple(
            FieldSpec(
                name=item["name"],
                data_type=FieldType(item["data_type"]),
                document_types=(
                    None if item.get("document_types") is None else tuple(item["document_types"])
                ),
                choices=tuple(item.get("choices", ())),
                required=item.get("required") is True,
                required_types=(
                    tuple(item["required"]) if isinstance(item.get("required"), list) else ()
                ),
                description=item.get("description"),
                assignment=Assignment(item.get("assignment", "auto")),
            )
            for item in data["fields"]
        )
        master = MasterDataSpec(
            contacts=tuple(data["contacts"]),
            document_types=tuple(data["document_types"]),
            tags=tuple(data["tags"]),
            fields=fields,
            contact_aliases={
                name: tuple(aliases) for name, aliases in data.get("contact_aliases", {}).items()
            },
            contact_identifiers={
                name: tuple(
                    IdentifierSpec(str(item["kind"]), str(item["value"]), item.get("topic"))
                    for item in identifiers
                )
                for name, identifiers in data.get("contact_identifiers", {}).items()
            },
            type_descriptions=dict(data.get("type_descriptions", {})),
            title_patterns=dict(data.get("title_patterns", {})),
            topics=tuple(_topic(item) for item in data.get("topics", ())),
            contact_topics={
                name: tuple(topics) for name, topics in data.get("contact_topics", {}).items()
            },
        )
    except (KeyError, TypeError, ValueError) as error:
        raise EvaluationSetError(f"master_data.json: {error!r}") from None
    topics = {name for name, _ in master.topics}
    for contact, enables in master.contact_topics.items():
        if set(enables) - topics:
            raise EvaluationSetError(
                f"master_data.json: contact_topics of {contact!r} names unknown topics "
                f"{set(enables) - topics}"
            )
    for contact, identifiers in master.contact_identifiers.items():
        enabled = set(master.contact_topics.get(contact, ()))
        wrong = {item.topic for item in identifiers if item.topic is not None} - enabled
        if wrong:
            raise EvaluationSetError(
                f"master_data.json: identifiers of {contact!r} point to topics it does not "
                f"enable: {wrong}"
            )
    for what, names, known in (
        ("contact_aliases", master.contact_aliases, master.contacts),
        ("contact_identifiers", master.contact_identifiers, master.contacts),
        ("contact_topics", master.contact_topics, master.contacts),
        ("type_descriptions", master.type_descriptions, master.document_types),
        ("title_patterns", master.title_patterns, master.document_types),
    ):
        if set(names) - set(known):
            raise EvaluationSetError(
                f"master_data.json: {what} names unknown {set(names) - set(known)}"
            )
    for definition in fields:
        unknown = set(definition.document_types or ()) - set(master.document_types)
        if unknown:
            raise EvaluationSetError(
                f"master_data.json: field {definition.name!r} names unknown types {unknown}"
            )
    return master


def _case(markdown: Path, master: MasterDataSpec) -> Case:
    name = markdown.stem
    data = _json(markdown.with_suffix(".json"))
    try:
        expected_data = data["expected"]
        expected = Expected(
            lane=Lane(expected_data["lane"]),
            contact=expected_data.get("contact"),
            document_type=expected_data.get("document_type"),
            tags=frozenset(expected_data.get("tags", ())),
            document_date=expected_data.get("document_date"),
            fields=dict(expected_data.get("fields", {})),
            lanes=_lanes(expected_data),
            topic=expected_data.get("topic"),
        )
        fake = None
        if data.get("fake") is not None:
            fake_data = data["fake"]
            fake = FakeAnswer(
                classification=fake_data["classification"],
                fields=dict(fake_data.get("fields", {})),
                lane=Lane(fake_data.get("lane", expected.lane.value)),
                # Without a lane of its own, the answer leads where the expected values do.
                lanes=(
                    _lanes(fake_data)
                    if "lane" in fake_data or "lanes" in fake_data
                    else dict(expected.lanes)
                ),
            )
        case = Case(
            name=name,
            description=str(data.get("description", "")),
            text=markdown.read_text(encoding="utf-8"),
            expected=expected,
            fake=fake,
            injection=bool(data.get("injection", False)),
            runs=frozenset(Run(run) for run in data.get("runs", list(Run))),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise EvaluationSetError(f"{name}: {error!r}") from None
    _check_names(case, master)
    return case


def _topic(item: Any) -> tuple[str, str | None]:
    """A topic of `topics`: a name, or an object with `name` and `parent`."""
    if isinstance(item, str):
        return item, None
    return str(item["name"]), item.get("parent")


def _lanes(data: Any) -> dict[Run, Lane]:
    return {Run(run): Lane(lane) for run, lane in data.get("lanes", {}).items()}


def _training(markdown: Path, master: MasterDataSpec) -> TrainingDocument:
    name = markdown.stem
    data = _json(markdown.with_suffix(".json"))
    try:
        document = TrainingDocument(
            name=name,
            text=markdown.read_text(encoding="utf-8"),
            contact=data.get("contact"),
            document_type=data.get("document_type"),
            tags=frozenset(data.get("tags", ())),
            document_date=data.get("document_date"),
            topic=data.get("topic"),
        )
    except (TypeError, ValueError) as error:
        raise EvaluationSetError(f"training/{name}: {error!r}") from None
    problems = _unknown_names(document.contact, document.document_type, document.tags, master)
    problems += _unknown_topic(document.contact, document.topic, master)
    if problems:
        raise EvaluationSetError(f"training/{name}: {', '.join(problems)}")
    return document


def _unknown_names(
    contact: str | None, document_type: str | None, tags: frozenset[str], master: MasterDataSpec
) -> list[str]:
    problems = []
    if contact is not None and contact not in master.contacts:
        problems.append(f"unknown contact {contact!r}")
    if document_type is not None and document_type not in master.document_types:
        problems.append(f"unknown document type {document_type!r}")
    return problems + [f"unknown tag {tag!r}" for tag in sorted(tags - set(master.tags))]


def _unknown_topic(contact: str | None, topic: str | None, master: MasterDataSpec) -> list[str]:
    """A topic must be one the contact enables."""
    if topic is None:
        return []
    if contact is None or topic not in master.contact_topics.get(contact, ()):
        return [f"topic {topic!r} is none {contact!r} enables"]
    return []


def _check_names(case: Case, master: MasterDataSpec) -> None:
    expected = case.expected
    problems = _unknown_names(expected.contact, expected.document_type, expected.tags, master)
    problems += _unknown_topic(expected.contact, expected.topic, master)
    names = {field.name for field in master.fields}
    problems += [f"unknown field {name!r}" for name in expected.fields if name not in names]
    if problems:
        raise EvaluationSetError(f"{case.name}: {', '.join(problems)}")
