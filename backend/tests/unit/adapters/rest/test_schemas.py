"""JSON forms of attribute values."""

from datetime import date
from decimal import Decimal

import pytest

from papiq.adapters.inbound.rest.schemas import MoneyValue, attribute_json
from papiq.core.domain.attributes import Money, Url


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("5.335E+7"), "53350000"),
        (Decimal("1E+2"), "100"),
        (Decimal("12.50"), "12.50"),
        (Decimal("-0.001"), "-0.001"),
        (Decimal("7"), "7"),
    ],
)
def test_numbers_are_written_in_plain_notation(value: Decimal, expected: str) -> None:
    assert attribute_json(value) == expected


def test_amounts_are_written_in_plain_notation() -> None:
    assert attribute_json(Money(Decimal("5.335E+7"), "EUR")) == MoneyValue(
        amount="53350000", currency="EUR"
    )
    assert attribute_json(Money(Decimal("84.20"), "EUR")) == MoneyValue(
        amount="84.20", currency="EUR"
    )


def test_other_types_keep_their_form() -> None:
    assert attribute_json("text") == "text"
    assert attribute_json(True) is True
    assert attribute_json(Url("https://example.org/x")) == "https://example.org/x"
    assert attribute_json(date(2026, 3, 31)) == "2026-03-31"
