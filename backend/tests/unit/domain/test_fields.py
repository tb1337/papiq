from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from papiq.core.domain.errors import ValidationError
from papiq.core.domain.fields import FieldDefinition, FieldType, Money, Url
from papiq.core.domain.ids import DocumentTypeId, new_id

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)


def definition(data_type: FieldType, choices: tuple[str, ...] = ()) -> FieldDefinition:
    return FieldDefinition.create(name="field", data_type=data_type, now=NOW, choices=choices)


ACCEPTED: list[tuple[FieldType, object, object]] = [
    (FieldType.TEXT, "Contract 42", "Contract 42"),
    (FieldType.NUMBER, Decimal("12.5"), Decimal("12.5")),
    (FieldType.NUMBER, 3, Decimal(3)),
    (FieldType.AMOUNT, Money(Decimal("99.90"), "EUR"), Money(Decimal("99.90"), "EUR")),
    (FieldType.DATE, date(2026, 10, 6), date(2026, 10, 6)),
    (FieldType.BOOLEAN, True, True),
    (FieldType.BOOLEAN, False, False),
    (FieldType.CHOICE, "monthly", "monthly"),
    (FieldType.LINK, Url("https://example.org/a?b=c"), Url("https://example.org/a?b=c")),
]

REJECTED: list[tuple[FieldType, object]] = [
    (FieldType.TEXT, ""),
    (FieldType.TEXT, "   "),
    (FieldType.TEXT, 42),
    (FieldType.NUMBER, 1.5),
    (FieldType.NUMBER, True),
    (FieldType.NUMBER, Decimal("NaN")),
    (FieldType.NUMBER, Decimal("Infinity")),
    (FieldType.NUMBER, "12"),
    (FieldType.AMOUNT, Decimal("12")),
    (FieldType.DATE, datetime(2026, 10, 6, tzinfo=UTC)),
    (FieldType.DATE, "2026-10-06"),
    (FieldType.BOOLEAN, 1),
    (FieldType.BOOLEAN, "true"),
    (FieldType.CHOICE, "weekly"),
    (FieldType.CHOICE, "Monthly"),
    (FieldType.LINK, "https://example.org"),
    (FieldType.TEXT, None),
]


@pytest.mark.parametrize(("data_type", "value", "expected"), ACCEPTED)
def test_value_fits_data_type(data_type: FieldType, value: object, expected: object) -> None:
    choices = ("monthly", "yearly") if data_type is FieldType.CHOICE else ()
    assert definition(data_type, choices).validate(value) == expected


@pytest.mark.parametrize(("data_type", "value"), REJECTED)
def test_value_of_wrong_type_is_rejected(data_type: FieldType, value: object) -> None:
    choices = ("monthly", "yearly") if data_type is FieldType.CHOICE else ()
    with pytest.raises(ValidationError, match="does not accept"):
        definition(data_type, choices).validate(value)


def test_every_data_type_is_covered() -> None:
    assert {row[0] for row in ACCEPTED} == set(FieldType)
    assert {row[0] for row in REJECTED} == set(FieldType)


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


def test_choices_belong_to_choice_fields_only() -> None:
    with pytest.raises(ValidationError, match="at least one choice"):
        definition(FieldType.CHOICE)
    with pytest.raises(ValidationError, match="unique"):
        definition(FieldType.CHOICE, ("a", "a"))
    with pytest.raises(ValidationError, match="only choice"):
        definition(FieldType.TEXT, ("a",))


def test_scope_is_global_or_bound_to_document_types() -> None:
    invoice, letter = DocumentTypeId(new_id()), DocumentTypeId(new_id())
    everywhere = definition(FieldType.TEXT)
    bound = FieldDefinition.create(
        name="IBAN", data_type=FieldType.TEXT, now=NOW, document_type_ids=[invoice]
    )
    assert everywhere.is_global
    assert everywhere.applies_to(None)
    assert everywhere.applies_to(letter)
    assert not bound.is_global
    assert bound.applies_to(invoice)
    assert not bound.applies_to(letter)
    assert not bound.applies_to(None)


def test_choices_and_scope_change() -> None:
    kind = FieldDefinition.create(
        name="Kind", data_type=FieldType.CHOICE, now=NOW, choices=["a", "b"]
    )
    assert kind.change_choices(["b", "c"]) == frozenset({"a"})
    assert kind.choices == ("b", "c")
    with pytest.raises(ValidationError):
        kind.change_choices([])
    with pytest.raises(ValidationError):
        kind.change_choices(["x", "x"])
    text = FieldDefinition.create(name="Note", data_type=FieldType.TEXT, now=NOW)
    with pytest.raises(ValidationError):
        text.change_choices(["a"])
    one, two = DocumentTypeId(new_id()), DocumentTypeId(new_id())
    assert text.change_scope([one, two]) is True  # global -> two types
    assert text.change_scope([one, two]) is False
    assert text.change_scope([one]) is True
    assert text.change_scope(None) is False  # global again
    with pytest.raises(ValidationError):
        text.change_scope([])
