from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from papiq.core.domain.attributes import AttributeDefinition, AttributeType, Money, Url
from papiq.core.domain.errors import ValidationError
from papiq.core.domain.ids import DocumentTypeId, new_id

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)


def definition(data_type: AttributeType, choices: tuple[str, ...] = ()) -> AttributeDefinition:
    return AttributeDefinition.create(name="field", data_type=data_type, now=NOW, choices=choices)


ACCEPTED: list[tuple[AttributeType, object, object]] = [
    (AttributeType.TEXT, "Contract 42", "Contract 42"),
    (AttributeType.NUMBER, Decimal("12.5"), Decimal("12.5")),
    (AttributeType.NUMBER, 3, Decimal(3)),
    (AttributeType.AMOUNT, Money(Decimal("99.90"), "EUR"), Money(Decimal("99.90"), "EUR")),
    (AttributeType.DATE, date(2026, 10, 6), date(2026, 10, 6)),
    (AttributeType.BOOLEAN, True, True),
    (AttributeType.BOOLEAN, False, False),
    (AttributeType.CHOICE, "monthly", "monthly"),
    (AttributeType.LINK, Url("https://example.org/a?b=c"), Url("https://example.org/a?b=c")),
]

REJECTED: list[tuple[AttributeType, object]] = [
    (AttributeType.TEXT, ""),
    (AttributeType.TEXT, "   "),
    (AttributeType.TEXT, 42),
    (AttributeType.NUMBER, 1.5),
    (AttributeType.NUMBER, True),
    (AttributeType.NUMBER, Decimal("NaN")),
    (AttributeType.NUMBER, Decimal("Infinity")),
    (AttributeType.NUMBER, "12"),
    (AttributeType.AMOUNT, Decimal("12")),
    (AttributeType.DATE, datetime(2026, 10, 6, tzinfo=UTC)),
    (AttributeType.DATE, "2026-10-06"),
    (AttributeType.BOOLEAN, 1),
    (AttributeType.BOOLEAN, "true"),
    (AttributeType.CHOICE, "weekly"),
    (AttributeType.CHOICE, "Monthly"),
    (AttributeType.LINK, "https://example.org"),
    (AttributeType.TEXT, None),
]


@pytest.mark.parametrize(("data_type", "value", "expected"), ACCEPTED)
def test_value_fits_data_type(data_type: AttributeType, value: object, expected: object) -> None:
    choices = ("monthly", "yearly") if data_type is AttributeType.CHOICE else ()
    assert definition(data_type, choices).validate(value) == expected


@pytest.mark.parametrize(("data_type", "value"), REJECTED)
def test_value_of_wrong_type_is_rejected(data_type: AttributeType, value: object) -> None:
    choices = ("monthly", "yearly") if data_type is AttributeType.CHOICE else ()
    with pytest.raises(ValidationError, match="does not accept"):
        definition(data_type, choices).validate(value)


def test_every_data_type_is_covered() -> None:
    assert {row[0] for row in ACCEPTED} == set(AttributeType)
    assert {row[0] for row in REJECTED} == set(AttributeType)


@pytest.mark.parametrize(
    ("amount", "currency"),
    [(Decimal("NaN"), "EUR"), (Decimal(1), "eur"), (Decimal(1), "EURO"), (1.5, "EUR")],
)
def test_money_needs_finite_decimal_and_iso_currency(amount: object, currency: str) -> None:
    with pytest.raises(ValidationError):
        Money(amount, currency)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "value",
    ["example.org", "ftp://example.org", "https://", "https://exa mple.org", "mailto:a@b.c"],
)
def test_url_must_be_absolute_http(value: str) -> None:
    with pytest.raises(ValidationError):
        Url(value)


def test_choices_belong_to_choice_attributes_only() -> None:
    with pytest.raises(ValidationError, match="at least one choice"):
        definition(AttributeType.CHOICE)
    with pytest.raises(ValidationError, match="unique"):
        definition(AttributeType.CHOICE, ("a", "a"))
    with pytest.raises(ValidationError, match="only choice"):
        definition(AttributeType.TEXT, ("a",))


def test_scope_is_global_or_bound_to_document_types() -> None:
    invoice, letter = DocumentTypeId(new_id()), DocumentTypeId(new_id())
    everywhere = definition(AttributeType.TEXT)
    bound = AttributeDefinition.create(
        name="IBAN", data_type=AttributeType.TEXT, now=NOW, document_type_ids=[invoice]
    )
    assert everywhere.is_global
    assert everywhere.applies_to(None)
    assert everywhere.applies_to(letter)
    assert not bound.is_global
    assert bound.applies_to(invoice)
    assert not bound.applies_to(letter)
    assert not bound.applies_to(None)
