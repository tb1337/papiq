from datetime import date
from decimal import Decimal

import pytest

from papiq.core.domain.evidence import (
    DocumentText,
    contains,
    currencies_in,
    dates_in,
    normalise,
    numbers_in,
)


def test_normalise() -> None:
    text = "**Rechnungs-\nnummer:**  4711\u00a0| Straße\tGROSS ﬁ #"
    assert normalise(text) == "rechnungsnummer: 4711 strasse gross fi"


@pytest.mark.parametrize(
    "written",
    [
        "31.03.2026",
        "31.3.2026",
        "31.03.26",
        "31/03/2026",
        "31-03-2026",
        "2026-03-31",
        "2026/03/31",
        "31. März 2026",
        "31. Maerz 2026",
        "31 Mär. 2026",
        "31. Mrz. 2026",
        "31st March 2026",
        "March 31, 2026",
        "Mar 31st 2026",
    ],
)
def test_dates_in_every_usual_notation(written: str) -> None:
    assert date(2026, 3, 31) in dates_in(normalise(f"Datum: {written}, Seite 1"))


def test_dates_need_a_whole_date() -> None:
    found = dates_in(normalise("Version 1.2.3.4, Zeitraum 03/2026, Konto 12.34.5678, 31.02.2026"))
    assert found == set()


def test_two_digit_years() -> None:
    assert dates_in("01.02.69 01.02.70") == {date(2069, 2, 1), date(1970, 2, 1)}


@pytest.mark.parametrize(
    ("written", "number"),
    [
        ("1.234,56", "1234.56"),
        ("1,234.56", "1234.56"),
        ("1 234,56", "1234.56"),
        ("1'234.56", "1234.56"),
        ("84,20", "84.2"),
        ("84.20", "84.2"),
        ("-84,20", "84.2"),
        ("2026", "2026"),
        ("1.000.000", "1000000"),
        ("0,125", "0.125"),
    ],
)
def test_numbers_in_german_and_english_notation(written: str, number: str) -> None:
    assert Decimal(number) in numbers_in(normalise(f"Betrag {written} EUR"))


def test_ambiguous_numbers_have_both_readings() -> None:
    assert {Decimal("1234"), Decimal("1.234")} <= numbers_in("1.234")


def test_dates_and_odd_groups_are_no_numbers() -> None:
    found = numbers_in(normalise("31.03.2026 1.23.456 12,3,4"))
    assert Decimal("31.03") not in found
    assert Decimal("123456") not in found


@pytest.mark.parametrize(
    ("written", "code"),
    [
        ("84,20 €", "EUR"),
        ("EUR 84,20", "EUR"),
        ("84,20 Euro", "EUR"),
        ("$84.20", "USD"),
        ("US$ 84.20", "USD"),
        ("84.20 USD", "USD"),
        ("£84.20", "GBP"),
        ("CHF 84.20", "CHF"),
        ("Fr. 84.20", "CHF"),
        ("84,20 kr", "SEK"),
    ],
)
def test_currencies(written: str, code: str) -> None:
    assert code in currencies_in(normalise(written))


def test_currency_not_shown() -> None:
    assert "USD" not in currencies_in(normalise("Betrag 84,20 €"))
    # Three-letter words that are no ISO 4217 code.
    assert currencies_in(normalise("bis den Sie von")) == set()


def test_contains() -> None:
    text = normalise("Mueller GmbH, IBAN DE12 3456 7890 1234 5678 90, Straße 1")
    assert contains(text, "Mueller GmbH")
    assert contains(text, "Müller GmbH")
    assert contains(text, "STRASSE 1")
    assert contains(text, "DE12345678901234567890")
    assert not contains(text, "Meier")
    assert not contains(text, "  ")
    assert not contains(normalise("1 2 3 4"), "1234")  # short values need their spaces
    assert not contains(normalise("Rechnung Z-3141"), "Z-3")  # whole words only
    assert not contains(normalise("vom 31.03.2026"), "31")
    assert contains(normalise("Rechnung Z-3141, bezahlt"), "Z-3141")


def test_document_text() -> None:
    facts = DocumentText("Rechnung vom 31.03.2026 über 84,20 €")
    assert facts.has_date(date(2026, 3, 31))
    assert not facts.has_date(date(2026, 3, 30))
    assert facts.has_number(Decimal("84.20"))
    assert facts.has_number(Decimal("-84.2"))
    assert facts.has_currency("EUR")
    assert facts.contains("rechnung")
