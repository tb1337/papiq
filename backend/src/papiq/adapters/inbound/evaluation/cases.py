"""The evaluation set on disk: master data, and per case a document as Markdown with what is
expected of it and, for runs without a model, a fixed answer.

Layout of a set directory:

- `master_data.json`: `contacts`, `document_types`, `tags` (names) and `attributes`
  (`name`, `data_type`, `document_types` (names, or null for global), `choices`).
- `cases/<name>.md`: the document text, as the parse step would produce it.
- `cases/<name>.json`: `description`, `expected` (`lane`, `contact`, `document_type`, `tags`,
  `document_date`, `attributes` by name, in the JSON form of the API) and `fake`
  (`classification`: the answer of the classify step; `attributes`: proposals by attribute
  name; `lane`: the lane this answer leads to, if not the expected one).
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from papiq.core.domain.attributes import AttributeType
from papiq.core.domain.json_value import JsonObject, JsonValue
from papiq.core.domain.pipeline import Lane
from papiq.core.ports import StructuredRequest


class EvaluationSetError(Exception):
    """The evaluation set is incomplete or malformed."""


@dataclass(frozen=True)
class AttributeSpec:
    name: str
    data_type: AttributeType
    document_types: tuple[str, ...] | None
    choices: tuple[str, ...] = ()


@dataclass(frozen=True)
class MasterDataSpec:
    contacts: tuple[str, ...]
    document_types: tuple[str, ...]
    tags: tuple[str, ...]
    attributes: tuple[AttributeSpec, ...]


@dataclass(frozen=True)
class Expected:
    lane: Lane
    contact: str | None
    document_type: str | None
    tags: frozenset[str]
    document_date: str | None
    attributes: dict[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True)
class FakeAnswer:
    classification: JsonObject
    attributes: dict[str, JsonValue]
    lane: Lane

    def respond(self, request: StructuredRequest) -> JsonObject:
        """The answer to a request of the classify or extract step."""
        if request.schema_name == "classification":
            return self.classification
        keys = dict(re.findall(r'^- (a\d+): "([^"\n]*)"', _before_document(request.user), re.M))
        empty: JsonObject = {"value": None, "evidence": None}
        return {"attributes": {key: self.attributes.get(name, empty) for key, name in keys.items()}}


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
        attributes = tuple(
            AttributeSpec(
                name=item["name"],
                data_type=AttributeType(item["data_type"]),
                document_types=(
                    None if item.get("document_types") is None else tuple(item["document_types"])
                ),
                choices=tuple(item.get("choices", ())),
            )
            for item in data["attributes"]
        )
        master = MasterDataSpec(
            contacts=tuple(data["contacts"]),
            document_types=tuple(data["document_types"]),
            tags=tuple(data["tags"]),
            attributes=attributes,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise EvaluationSetError(f"master_data.json: {error!r}") from None
    for attribute in attributes:
        unknown = set(attribute.document_types or ()) - set(master.document_types)
        if unknown:
            raise EvaluationSetError(
                f"master_data.json: attribute {attribute.name!r} names unknown types {unknown}"
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
            attributes=dict(expected_data.get("attributes", {})),
        )
        fake = None
        if data.get("fake") is not None:
            fake_data = data["fake"]
            fake = FakeAnswer(
                classification=fake_data["classification"],
                attributes=dict(fake_data.get("attributes", {})),
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
    names = {attribute.name for attribute in master.attributes}
    problems += [f"unknown attribute {name!r}" for name in expected.attributes if name not in names]
    if problems:
        raise EvaluationSetError(f"{case.name}: {', '.join(problems)}")
