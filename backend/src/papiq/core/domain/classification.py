"""Results of the judge and the fields node, field by field.

Each value the signals proposed is checked against facts; a `FieldCheck` records the proposal,
the candidates with their sources, the check and its outcome. Checks are stored in the
processing log (output of the judge and the fields node), so what was proposed stays traceable
after a person corrected it; the inbox and the rules (M7) read them from there.
"""

from dataclasses import dataclass
from typing import Self

from papiq.core.domain.errors import ValidationError
from papiq.core.domain.ids import FieldId
from papiq.core.domain.json_value import JsonObject, JsonValue
from papiq.core.domain.locations import Location, locations_from_json, locations_to_json
from papiq.core.domain.pipeline import Outcome

CONTACT = "contact"
DOCUMENT_TYPE = "document_type"
TOPIC = "topic"
TAGS = "tags"
DOCUMENT_DATE = "document_date"
TITLE = "title"
_FIELD_PREFIX = "field:"


def field_key(id: FieldId) -> str:
    return f"{_FIELD_PREFIX}{id}"


def field_id_of(field: str) -> str | None:
    """The field id named by a check name, or None for the other checks."""
    return field.removeprefix(_FIELD_PREFIX) if field.startswith(_FIELD_PREFIX) else None


@dataclass(frozen=True, kw_only=True)
class CandidateSource:
    """What one signal says about a candidate: `names` whether it is sure enough to name it
    (it counts for the judge's rule), `score` its match, probability or share of the similar
    documents, `threshold` the score from which it names a value, `votes` of `of` similar
    documents (`documents`) that carry it and the highest `similarity` among them, the `quote`
    the language model gave, the `identifiers` (kinds) of the contact found in the text, and
    whether its name or an alias stands in the text (`named_in_text`)."""

    signal: str  # the node: signal_llm, signal_classifier, signal_neighbours, signal_facts
    names: bool = False
    score: float | None = None
    threshold: float | None = None
    similarity: float | None = None
    votes: int | None = None
    of: int | None = None
    quote: str | None = None
    identifiers: tuple[str, ...] = ()
    documents: tuple[str, ...] = ()
    named_in_text: bool = False

    def to_json(self) -> JsonObject:
        data: JsonObject = {"signal": self.signal, "names": self.names}
        for key, number in (
            ("score", self.score),
            ("threshold", self.threshold),
            ("similarity", self.similarity),
        ):
            if number is not None:
                data[key] = round(number, 4)
        for key, value in (("votes", self.votes), ("of", self.of), ("quote", self.quote)):
            if value is not None:
                data[key] = value
        if self.identifiers:
            data["identifiers"] = list(self.identifiers)
        if self.documents:
            data["documents"] = list(self.documents)
        if self.named_in_text:
            data["named_in_text"] = True
        return data

    @classmethod
    def from_json(cls, data: JsonValue) -> Self:
        if not isinstance(data, dict) or not isinstance(data.get("signal"), str):
            raise ValidationError(f"invalid candidate source {data!r}")
        return cls(
            signal=str(data["signal"]),
            names=data.get("names") is True,
            score=_optional_number(data.get("score")),
            threshold=_optional_number(data.get("threshold")),
            similarity=_optional_number(data.get("similarity")),
            votes=_optional_int(data.get("votes")),
            of=_optional_int(data.get("of")),
            quote=_optional_text(data.get("quote")),
            identifiers=_texts(data.get("identifiers")),
            documents=_texts(data.get("documents")),
            named_in_text=data.get("named_in_text") is True,
        )


@dataclass(frozen=True, kw_only=True)
class Candidate:
    """A value the signals proposed for a field, best first: `value` as the check's (a contact
    or type id, a tag id) or None for a name that does not exist yet (`label`), with what each
    signal said, and whether a signal named another value against it (`contradicted`)."""

    value: JsonValue
    label: str
    sources: tuple[CandidateSource, ...] = ()
    contradicted: bool = False

    def to_json(self) -> JsonObject:
        return {
            "value": self.value,
            "label": self.label,
            "sources": [source.to_json() for source in self.sources],
            "contradicted": self.contradicted,
        }

    @classmethod
    def from_json(cls, data: JsonValue) -> Self:
        if not isinstance(data, dict) or not isinstance(data.get("label"), str):
            raise ValidationError(f"invalid candidate {data!r}")
        sources = data.get("sources")
        return cls(
            value=data.get("value"),
            label=str(data["label"]),
            sources=tuple(
                CandidateSource.from_json(item)
                for item in (sources if isinstance(sources, list) else [])
            ),
            contradicted=data.get("contradicted") is True,
        )


@dataclass(frozen=True, kw_only=True)
class FieldCheck:
    """One field of a model's proposal and its check.

    - `proposed`: what the model answered for the field, unchanged.
    - `evidence`: the excerpt the model quoted, if any.
    - `value`: the checked value as JSON (contact, type or topic id, tag ids, ISO date,
      field value), set if the field is OK; it was applied to the document.
    - `suggestion`: for an uncertain field, a value a person may accept as it is (an existing
      contact, a date or field value that is valid but not shown in the text).
    - `new_name`: the name of a contact or type the model proposed that does not exist; only
      an admin can create it.
    - `quote_shows_value`: whether the quoted passage itself shows the proposed value (a
      contact's name, the date, the amount), or only leads to it (a customer number for the
      contact, a passage for a yes/no field); None without a quote.
    - `locations`: where the quote stands on the pages of the archive PDF; none if it was not
      found there (or before this was recorded).
    - `candidates`: the values the signals proposed, best first, with their sources (the
      judge's checks; none in others, and in entries from before the signals).
    """

    field: str
    outcome: Outcome
    confidence: float
    reason: str | None = None
    proposed: JsonValue = None
    evidence: str | None = None
    value: JsonValue = None
    suggestion: JsonValue = None
    new_name: str | None = None
    quote_shows_value: bool | None = None
    locations: tuple[Location, ...] = ()
    candidates: tuple[Candidate, ...] = ()

    def __post_init__(self) -> None:
        if self.outcome is Outcome.FAILED:
            raise ValidationError("a field is either OK or uncertain")
        if self.outcome is Outcome.UNCERTAIN and not self.reason:
            raise ValidationError(f"uncertain field {self.field} needs a reason")
        if not 0 <= self.confidence <= 1:
            raise ValidationError(f"confidence must be between 0 and 1, got {self.confidence}")

    @property
    def ok(self) -> bool:
        return self.outcome is Outcome.OK

    def to_json(self) -> JsonObject:
        return {
            "field": self.field,
            "outcome": self.outcome.value,
            "confidence": round(self.confidence, 4),
            "reason": self.reason,
            "proposed": self.proposed,
            "evidence": self.evidence,
            "value": self.value,
            "suggestion": self.suggestion,
            "new_name": self.new_name,
            "quote_shows_value": self.quote_shows_value,
            "locations": locations_to_json(self.locations),
            "candidates": [candidate.to_json() for candidate in self.candidates],
        }

    @classmethod
    def from_json(cls, data: JsonValue) -> Self:
        if not isinstance(data, dict):
            raise ValidationError(f"invalid field check {data!r}")
        try:
            confidence = data["confidence"]
            if not isinstance(confidence, int | float) or isinstance(confidence, bool):
                raise ValidationError(f"invalid confidence {confidence!r}")
            return cls(
                field=str(data["field"]),
                outcome=Outcome(str(data["outcome"])),
                confidence=float(confidence),
                reason=_optional_text(data.get("reason")),
                proposed=data.get("proposed"),
                evidence=_optional_text(data.get("evidence")),
                value=data.get("value"),
                suggestion=data.get("suggestion"),
                new_name=_optional_text(data.get("new_name")),
                quote_shows_value=_optional_bool(data.get("quote_shows_value")),
                locations=locations_from_json(data.get("locations")),
                candidates=_candidates(data.get("candidates")),
            )
        except (KeyError, ValueError) as error:
            raise ValidationError(f"invalid field check: {error}") from None


def checks_to_json(checks: list[FieldCheck]) -> list[JsonValue]:
    return [check.to_json() for check in checks]


def checks_from_json(data: JsonValue) -> list[FieldCheck]:
    """The checks of a log entry's output (`fields`); none if there are none."""
    if not isinstance(data, list):
        return []
    return [FieldCheck.from_json(item) for item in data]


def _optional_text(value: JsonValue) -> str | None:
    return value if isinstance(value, str) else None


def _optional_bool(value: JsonValue) -> bool | None:
    return value if isinstance(value, bool) else None


def _optional_number(value: JsonValue) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _optional_int(value: JsonValue) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _texts(value: JsonValue) -> tuple[str, ...]:
    return tuple(item for item in value if isinstance(item, str)) if isinstance(value, list) else ()


def _candidates(value: JsonValue) -> tuple[Candidate, ...]:
    return tuple(Candidate.from_json(item) for item in value) if isinstance(value, list) else ()
