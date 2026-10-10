"""JSON Schemas of the model's answers, and checking an answer's structure.

The schemas are built per document from the master data (lists as `enum`); every property is
required, nothing else is allowed, "not found" is `null`. The check is strict about structure
(an answer with a missing or an extra field is invalid, since an extra field may be an
instruction from the document at work) and lenient about values: a document type outside the
list counts as a proposed new type, unknown tags as proposed new tags, and a value that cannot
be read makes only its field uncertain.
"""

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass

from papiq.core.domain.fields import FieldDefinition, FieldType
from papiq.core.domain.json_value import JsonObject, JsonValue

_EVIDENCE_LENGTH = 500  # characters of a quoted passage that are kept
_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)

_TEXT_OR_NULL: JsonObject = {"type": ["string", "null"]}


class AnswerError(ValueError):
    """The answer is not JSON or does not have the structure of the schema."""


@dataclass(frozen=True, kw_only=True)
class Proposal:
    """A proposed value with the passage the model quoted for it."""

    value: JsonValue
    evidence: str | None


@dataclass(frozen=True, kw_only=True)
class ClassifyAnswer:
    contact: Proposal  # value: the name, or None
    document_type: str | None
    new_document_type: str | None
    tags: list[str]
    new_tags: list[str]
    document_date: Proposal  # value: ISO date text, or None
    raw: JsonObject


# --- classification ---------------------------------------------------------------------------

_CLASSIFY_FIELDS = (
    "contact",
    "document_type",
    "new_document_type",
    "tags",
    "new_tags",
    "document_date",
)


def classify_schema(document_types: Sequence[str], tags: Sequence[str]) -> JsonObject:
    tag_items: JsonObject = {"type": "string"}
    if tags:
        tag_items["enum"] = list(tags)
    tag_list: JsonObject = {"type": "array", "items": tag_items}
    if not tags:
        tag_list["maxItems"] = 0
    return _object(
        {
            "contact": _proposal({"type": ["string", "null"]}),
            "document_type": {"type": ["string", "null"], "enum": [*document_types, None]},
            "new_document_type": _TEXT_OR_NULL,
            "tags": tag_list,
            "new_tags": {"type": "array", "items": {"type": "string"}},
            "document_date": _proposal(_TEXT_OR_NULL),
        }
    )


def parse_classify(content: str) -> ClassifyAnswer:
    data = _load(content)
    _require_fields(data, _CLASSIFY_FIELDS, "the answer")
    return ClassifyAnswer(
        contact=_parse_proposal(data["contact"], "contact", text=True),
        document_type=_text_or_null(data["document_type"], "document_type"),
        new_document_type=_text_or_null(data["new_document_type"], "new_document_type"),
        tags=_texts(data["tags"], "tags"),
        new_tags=_texts(data["new_tags"], "new_tags"),
        document_date=_parse_proposal(data["document_date"], "document_date", text=True),
        raw=data,
    )


# --- fields -------------------------------------------------------------------------------


def field_keys(definitions: Sequence[FieldDefinition]) -> dict[str, FieldDefinition]:
    """Short keys for the fields (`a1`, `a2`, ...): names may be any text, keys are safe
    as JSON property names in every provider's schema dialect."""
    return {f"a{index}": definition for index, definition in enumerate(definitions, start=1)}


def extract_schema(keys: dict[str, FieldDefinition]) -> JsonObject:
    return _object(
        {
            "fields": _object(
                {key: _proposal(_value_schema(definition)) for key, definition in keys.items()}
            )
        }
    )


def parse_extract(content: str, keys: Sequence[str]) -> dict[str, Proposal]:
    data = _load(content)
    _require_fields(data, ("fields",), "the answer")
    fields = data["fields"]
    if not isinstance(fields, dict):
        raise AnswerError("fields: expected an object")
    _require_fields(fields, keys, "fields")
    return {key: _parse_proposal(fields[key], f"fields.{key}") for key in keys}


def _value_schema(definition: FieldDefinition) -> JsonObject:
    match definition.data_type:
        case FieldType.AMOUNT:
            amount: JsonObject = {
                "type": "object",
                "properties": {"amount": {"type": "string"}, "currency": {"type": "string"}},
                "required": ["amount", "currency"],
                "additionalProperties": False,
            }
            return {"anyOf": [amount, {"type": "null"}]}
        case FieldType.BOOLEAN:
            return {"type": ["boolean", "null"]}
        case FieldType.CHOICE:
            return {"type": ["string", "null"], "enum": [*definition.choices, None]}
        case _:
            return dict(_TEXT_OR_NULL)


# --- helpers ----------------------------------------------------------------------------------


def _object(properties: JsonObject) -> JsonObject:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _proposal(value: JsonObject) -> JsonObject:
    return _object({"value": value, "evidence": dict(_TEXT_OR_NULL)})


def _load(content: str) -> JsonObject:
    fenced = _FENCE.match(content)
    text = fenced.group(1) if fenced else content
    try:
        data = json.loads(text)
    except ValueError:
        raise AnswerError("the answer is not JSON") from None
    if not isinstance(data, dict):
        raise AnswerError("the answer is not a JSON object")
    return data


def _require_fields(data: JsonObject, fields: Sequence[str], where: str) -> None:
    missing = [name for name in fields if name not in data]
    extra = sorted(set(data) - set(fields))
    if missing:
        raise AnswerError(f"{where}: missing {', '.join(missing)}")
    if extra:
        raise AnswerError(f"{where}: unexpected {', '.join(extra)}")


def _parse_proposal(data: JsonValue, where: str, *, text: bool = False) -> Proposal:
    if not isinstance(data, dict):
        raise AnswerError(f"{where}: expected an object with value and evidence")
    _require_fields(data, ("value", "evidence"), where)
    value = data["value"]
    if text:
        value = _text_or_null(value, f"{where}.value")
    evidence = _text_or_null(data["evidence"], f"{where}.evidence")
    return Proposal(value=value, evidence=evidence[:_EVIDENCE_LENGTH] if evidence else None)


def _text_or_null(value: JsonValue, where: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise AnswerError(f"{where}: expected text or null")
    return value.strip() or None


def _texts(value: JsonValue, where: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise AnswerError(f"{where}: expected a list of texts")
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]
