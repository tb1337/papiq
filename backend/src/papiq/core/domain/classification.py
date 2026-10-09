"""Results of classification and field extraction, field by field.

Each field the model proposed is checked against facts; a `FieldCheck` records the proposal,
the check and its outcome. Checks are stored in the processing log (output of the classify and
extract steps), so what the model proposed stays traceable after a person corrected it; the
inbox and the rules (M7) read them from there.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Self

from papiq.core.domain.errors import ValidationError
from papiq.core.domain.fields import (
    FieldDefinition,
    FieldType,
    FieldValue,
    Money,
    Url,
)
from papiq.core.domain.ids import FieldId
from papiq.core.domain.json_value import JsonObject, JsonValue
from papiq.core.domain.pipeline import Outcome

CONTACT = "contact"
DOCUMENT_TYPE = "document_type"
TAGS = "tags"
DOCUMENT_DATE = "document_date"
_FIELD_PREFIX = "field:"


def field_key(id: FieldId) -> str:
    return f"{_FIELD_PREFIX}{id}"


def field_id_of(field: str) -> str | None:
    """The field id named by a check name, or None for the other checks."""
    return field.removeprefix(_FIELD_PREFIX) if field.startswith(_FIELD_PREFIX) else None


@dataclass(frozen=True, kw_only=True)
class FieldCheck:
    """One field of a model's proposal and its check.

    - `proposed`: what the model answered for the field, unchanged.
    - `evidence`: the excerpt the model quoted, if any.
    - `value`: the checked value as JSON (contact or type id, tag ids, ISO date, field
      value), set if the field is OK; it was applied to the document.
    - `suggestion`: for an uncertain field, a value a person may accept as it is (an existing
      contact, a date or field value that is valid but not shown in the text).
    - `new_name`: the name of a contact or type the model proposed that does not exist; only
      an admin can create it.
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


# --- field values as JSON -----------------------------------------------------------------


def field_to_json(value: FieldValue) -> JsonValue:
    """Text, choice and link as text; number as decimal text; amount as `{amount, currency}`;
    date as ISO text; yes/no as boolean."""
    match value:
        case bool() | str():
            return value
        case Decimal():
            return str(value)
        case Money():
            return {"amount": str(value.amount), "currency": value.currency}
        case date():
            return value.isoformat()
        case Url():
            return value.value


def field_from_json(definition: FieldDefinition, data: JsonValue) -> FieldValue:
    """The value of `definition` from its JSON form (as `field_to_json` writes it), checked
    against the definition. ValidationError if it does not fit."""
    value: object = data
    try:
        match definition.data_type:
            case FieldType.NUMBER if isinstance(data, str | int) and not isinstance(data, bool):
                value = Decimal(str(data))
            case FieldType.AMOUNT if isinstance(data, dict):
                amount, currency = data.get("amount"), data.get("currency")
                if isinstance(amount, str | int) and not isinstance(amount, bool):
                    value = Money(Decimal(str(amount)), str(currency))
            case FieldType.DATE if isinstance(data, str):
                value = date.fromisoformat(data)
            case FieldType.LINK if isinstance(data, str):
                value = Url(data)
    except (InvalidOperation, ValueError, ValidationError):
        raise ValidationError(
            f"field '{definition.name}' ({definition.data_type}) does not accept {data!r}"
        ) from None
    return definition.validate(value)
