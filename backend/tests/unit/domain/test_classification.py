from datetime import date
from decimal import Decimal

import pytest

from papiq.core.domain.attributes import AttributeDefinition, AttributeType, Money, Url
from papiq.core.domain.classification import (
    FieldCheck,
    attribute_field,
    attribute_from_json,
    attribute_of,
    attribute_to_json,
    checks_from_json,
    checks_to_json,
)
from papiq.core.domain.errors import ValidationError
from papiq.core.domain.json_value import JsonValue
from papiq.core.domain.pipeline import Outcome
from tests.builders import NOW


def test_field_check_round_trip() -> None:
    checks = [
        FieldCheck(field="contact", outcome=Outcome.OK, confidence=0.95, value="id", proposed="X"),
        FieldCheck(
            field="document_date",
            outcome=Outcome.UNCERTAIN,
            confidence=0,
            reason="the date does not appear in the text",
            proposed="2026-03-31",
            evidence="31.03.",
            suggestion="2026-03-31",
        ),
    ]
    assert checks_from_json(checks_to_json(checks)) == checks
    assert checks_from_json(None) == []


def test_field_check_rules() -> None:
    with pytest.raises(ValidationError, match="needs a reason"):
        FieldCheck(field="contact", outcome=Outcome.UNCERTAIN, confidence=0)
    with pytest.raises(ValidationError, match="OK or uncertain"):
        FieldCheck(field="contact", outcome=Outcome.FAILED, confidence=0, reason="x")
    with pytest.raises(ValidationError, match="between 0 and 1"):
        FieldCheck(field="contact", outcome=Outcome.OK, confidence=2)
    with pytest.raises(ValidationError):
        FieldCheck.from_json({"field": "contact", "outcome": "ok", "confidence": "high"})
    with pytest.raises(ValidationError):
        FieldCheck.from_json({"field": "contact"})


def test_attribute_fields() -> None:
    definition = AttributeDefinition.create(name="Betrag", data_type=AttributeType.AMOUNT, now=NOW)
    field = attribute_field(definition.id)
    assert attribute_of(field) == str(definition.id)
    assert attribute_of("contact") is None


@pytest.mark.parametrize(
    ("data_type", "value", "json"),
    [
        (AttributeType.TEXT, "R-4711", "R-4711"),
        (AttributeType.NUMBER, Decimal("12.5"), "12.5"),
        (
            AttributeType.AMOUNT,
            Money(Decimal("84.20"), "EUR"),
            {"amount": "84.20", "currency": "EUR"},
        ),
        (AttributeType.DATE, date(2026, 3, 31), "2026-03-31"),
        (AttributeType.BOOLEAN, True, True),
        (AttributeType.LINK, Url("https://example.org/a"), "https://example.org/a"),
    ],
)
def test_attribute_values_as_json(data_type: AttributeType, value: object, json: object) -> None:
    definition = AttributeDefinition.create(name="A", data_type=data_type, now=NOW)
    assert attribute_to_json(definition.validate(value)) == json
    assert attribute_from_json(definition, json) == value


def test_attribute_values_from_json_are_checked() -> None:
    choice = AttributeDefinition.create(
        name="Art", data_type=AttributeType.CHOICE, choices=["a", "b"], now=NOW
    )
    assert attribute_from_json(choice, "a") == "a"
    cases: list[tuple[AttributeDefinition, JsonValue]] = [
        (choice, "c"),
        (AttributeDefinition.create(name="N", data_type=AttributeType.NUMBER, now=NOW), "x"),
        (AttributeDefinition.create(name="D", data_type=AttributeType.DATE, now=NOW), "31.03."),
        (
            AttributeDefinition.create(name="M", data_type=AttributeType.AMOUNT, now=NOW),
            {"amount": "1", "currency": "euro"},
        ),
    ]
    for definition, data in cases:
        with pytest.raises(ValidationError):
            attribute_from_json(definition, data)
