"""JSON Schemas of the model's answers, and checking an answer's structure.

The schemas are built per document from the master data (lists as `enum`); every property is
required, nothing else is allowed, "not found" is `null`. They keep to what strict validators
accept (Claude's too): one of a list or null as `anyOf`, no limits on arrays. The check is
strict about structure (an answer with a missing or an extra field is invalid, since an extra
field may be an instruction from the document at work) and lenient about values: a document
type outside the list counts as a proposed new type, unknown tags as proposed new tags, and a
value that cannot be read makes only its field uncertain.
"""

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass

from papiq.core.domain.fields import FieldDefinition, FieldType
from papiq.core.domain.json_value import JsonObject, JsonValue
from papiq.core.domain.master_data import one_line

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


# --- the proposal (`signal_llm`) --------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class ProposeAnswer:
    sender: Proposal  # value: the name, or None
    recipient: str | None
    document_type: str | None
    new_document_type: str | None
    topic: str | None
    tags: list[str]
    new_tags: list[str]
    document_date: Proposal  # value: the date as written, or None
    title: str | None
    language: str | None
    fields: dict[str, Proposal]  # by key; only the fields the model gave
    raw: JsonObject


_PROPOSE_FIELDS = (
    "sender",
    "recipient",
    "document_type",
    "new_document_type",
    "topic",
    "tags",
    "new_tags",
    "document_date",
    "title",
    "language",
    "fields",
)
_TITLE_LENGTH = 200
_FIELD_ENTRY = ("key", "value", "evidence")


def propose_schema(
    document_types: Sequence[str],
    tags: Sequence[str],
    keys: Sequence[str],
    topics: Sequence[str] = (),
) -> JsonObject:
    """Every field value is text, as written in the document; the fields come as a list of
    the keys the document shows (the model leaves the others out). `topics`: those of the
    listed contacts, one of them or null."""
    # Without tags or fields to list, anything given is left out or proposed as new.
    tag_items: JsonObject = {"type": "string"}
    if tags:
        tag_items["enum"] = list(tags)
    key: JsonObject = {"type": "string"}
    if keys:
        key["enum"] = list(keys)
    entry = _object({"key": key, "value": dict(_TEXT_OR_NULL), "evidence": dict(_TEXT_OR_NULL)})
    return _object(
        {
            "sender": _proposal(dict(_TEXT_OR_NULL)),
            "recipient": dict(_TEXT_OR_NULL),
            "document_type": _one_of_or_null(document_types),
            "new_document_type": dict(_TEXT_OR_NULL),
            "topic": _one_of_or_null(topics),
            "tags": {"type": "array", "items": tag_items},
            "new_tags": {"type": "array", "items": {"type": "string"}},
            "document_date": _proposal(dict(_TEXT_OR_NULL)),
            "title": dict(_TEXT_OR_NULL),
            "language": dict(_TEXT_OR_NULL),
            "fields": {"type": "array", "items": entry},
        }
    )


def parse_propose(content: str, keys: Sequence[str]) -> ProposeAnswer:
    """Strict about the structure; a field entry with a key that is not listed is left out,
    and of two entries for one key the first counts."""
    data = _load(content)
    _require_fields(data, _PROPOSE_FIELDS, "the answer")
    entries = data["fields"]
    if not isinstance(entries, list):
        raise AnswerError("fields: expected a list")
    fields: dict[str, Proposal] = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise AnswerError(f"fields[{index}]: expected an object")
        _require_fields(entry, _FIELD_ENTRY, f"fields[{index}]")
        key = entry["key"]
        if isinstance(key, str) and key in keys and key not in fields:
            proposal = _parse_proposal(
                {"value": entry["value"], "evidence": entry["evidence"]}, f"fields[{index}]"
            )
            fields[key] = proposal
    # The title becomes the document's: on one line, without control characters, not too long.
    title = _text_or_null(data["title"], "title")
    title = one_line(title)[:_TITLE_LENGTH].strip() or None if title else None
    return ProposeAnswer(
        sender=_parse_proposal(data["sender"], "sender", text=True),
        recipient=_text_or_null(data["recipient"], "recipient"),
        document_type=_text_or_null(data["document_type"], "document_type"),
        new_document_type=_text_or_null(data["new_document_type"], "new_document_type"),
        topic=_text_or_null(data["topic"], "topic"),
        tags=_texts(data["tags"], "tags"),
        new_tags=_texts(data["new_tags"], "new_tags"),
        document_date=_parse_proposal(data["document_date"], "document_date", text=True),
        title=title,
        language=_text_or_null(data["language"], "language"),
        fields=fields,
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
        case FieldType.PERIOD:
            period: JsonObject = {
                "type": "object",
                "properties": {"start": {"type": "string"}, "end": {"type": "string"}},
                "required": ["start", "end"],
                "additionalProperties": False,
            }
            return {"anyOf": [period, {"type": "null"}]}
        case FieldType.BOOLEAN:
            return {"type": ["boolean", "null"]}
        case FieldType.CHOICE:
            return _one_of_or_null(definition.choices)
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


def _one_of_or_null(names: Sequence[str]) -> JsonObject:
    """One of `names`, or null; any text without names."""
    if not names:
        return dict(_TEXT_OR_NULL)
    return {"anyOf": [{"type": "string", "enum": list(names)}, {"type": "null"}]}


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
