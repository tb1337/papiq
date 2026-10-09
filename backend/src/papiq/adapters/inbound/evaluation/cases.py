"""The evaluation set on disk: master data, and per case a document as Markdown with what is
expected of it and, for runs without a model, a fixed answer.

Layout of a set directory:

- `master_data.json`: `contacts`, `document_types`, `tags` (names) and `fields`
  (`name`, `data_type`, `document_types` (names, or null for global), `choices`).
- `cases/<name>.md`: the document text, as the parse step would produce it.
- `cases/<name>.json`: `description`, `expected` (`lane`, `contact`, `document_type`, `tags`,
  `document_date`, `fields` by name, in the JSON form of the API) and `fake`
  (`classification`: the answer of the classify step; `fields`: proposals by field
  name; `lane`: the lane this answer leads to, if not the expected one).
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from papiq.core.domain.fields import FieldType
from papiq.core.domain.json_value import JsonObject, JsonValue
from papiq.core.domain.pipeline import Lane
from papiq.core.ports import StructuredRequest


class EvaluationSetError(Exception):
    """The evaluation set is incomplete or malformed."""


@dataclass(frozen=True)
class FieldSpec:
    name: str
    data_type: FieldType
    document_types: tuple[str, ...] | None
    choices: tuple[str, ...] = ()


@dataclass(frozen=True)
class MasterDataSpec:
    contacts: tuple[str, ...]
    document_types: tuple[str, ...]
    tags: tuple[str, ...]
    fields: tuple[FieldSpec, ...]


@dataclass(frozen=True)
class Expected:
    lane: Lane
    contact: str | None
    document_type: str | None
    tags: frozenset[str]
    document_date: str | None
    fields: dict[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True)
class FakeAnswer:
    classification: JsonObject
    fields: dict[str, JsonValue]
    lane: Lane

    def respond(self, request: StructuredRequest) -> JsonObject:
        """The answer to a request of the classify or extract step."""
        if request.schema_name == "classification":
            return self.classification
        keys = dict(re.findall(r'^- (a\d+): "([^"\n]*)"', _before_document(request.user), re.M))
        empty: JsonObject = {"value": None, "evidence": None}
        return {"fields": {key: self.fields.get(name, empty) for key, name in keys.items()}}


@dataclass(frozen=True)
class Case:
    name: str
    description: str
    text: str
    expected: Expected
    fake: FakeAnswer | None
    injection: bool = False


@dataclass(frozen=True)
class EvaluationSet:
    path: Path
    master_data: MasterDataSpec
    cases: tuple[Case, ...]


def load_set(path: Path) -> EvaluationSet:
    """Read and check the set in `path`. EvaluationSetError names what is wrong."""
    master = _master_data(_json(path / "master_data.json"))
    directory = path / "cases"
    if not directory.is_dir():
        raise EvaluationSetError(f"{directory}: no cases directory")
    cases = tuple(_case(markdown, master) for markdown in sorted(directory.glob("*.md")))
    if not cases:
        raise EvaluationSetError(f"{directory}: no cases")
    return EvaluationSet(path, master, cases)


def _before_document(message: str) -> str:
    return message.split("The document follows", 1)[0]


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
            )
            for item in data["fields"]
        )
        master = MasterDataSpec(
            contacts=tuple(data["contacts"]),
            document_types=tuple(data["document_types"]),
            tags=tuple(data["tags"]),
            fields=fields,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise EvaluationSetError(f"master_data.json: {error!r}") from None
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
        )
        fake = None
        if data.get("fake") is not None:
            fake_data = data["fake"]
            fake = FakeAnswer(
                classification=fake_data["classification"],
                fields=dict(fake_data.get("fields", {})),
                lane=Lane(fake_data.get("lane", expected.lane.value)),
            )
        case = Case(
            name=name,
            description=str(data.get("description", "")),
            text=markdown.read_text(encoding="utf-8"),
            expected=expected,
            fake=fake,
            injection=bool(data.get("injection", False)),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise EvaluationSetError(f"{name}: {error!r}") from None
    _check_names(case, master)
    return case


def _check_names(case: Case, master: MasterDataSpec) -> None:
    expected = case.expected
    problems = []
    if expected.contact is not None and expected.contact not in master.contacts:
        problems.append(f"unknown contact {expected.contact!r}")
    if expected.document_type is not None and expected.document_type not in master.document_types:
        problems.append(f"unknown document type {expected.document_type!r}")
    problems += [f"unknown tag {tag!r}" for tag in sorted(expected.tags - set(master.tags))]
    names = {field.name for field in master.fields}
    problems += [f"unknown field {name!r}" for name in expected.fields if name not in names]
    if problems:
        raise EvaluationSetError(f"{case.name}: {', '.join(problems)}")
