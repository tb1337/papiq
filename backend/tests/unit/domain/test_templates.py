from datetime import date
from decimal import Decimal

import pytest

from papiq.core.domain.fields import FieldDefinition, FieldType, Money, Url, template_key
from papiq.core.domain.ids import SpaceId, UserId, new_id
from papiq.core.domain.templates import (
    TemplateValue,
    template_values,
    unknown_values,
    writing_templates,
)
from tests import builders


@pytest.mark.parametrize(
    ("name", "key"),
    [
        ("LA_Brutto", "la_brutto"),
        ("Fällig am", "faellig_am"),
        ("Straße", "strasse"),
        ("Café-Nr.", "cafe_nr"),
        ("  Rechnungs  Nummer ", "rechnungs_nummer"),
        ("2. Mahnung", "f_2_mahnung"),
        ("€", "field"),
    ],
)
def test_a_field_s_key_in_templates(name: str, key: str) -> None:
    assert template_key(name) == key
    definition = FieldDefinition.create(name=name, data_type=FieldType.TEXT, now=builders.NOW)
    assert definition.key == key


def test_the_values_of_a_document() -> None:
    document = builders.document(UserId(new_id()), SpaceId(new_id()), filename="Scan 2026-03.pdf")
    amount = FieldDefinition.create(name="LA_Brutto", data_type=FieldType.AMOUNT, now=builders.NOW)
    link = FieldDefinition.create(name="Portal", data_type=FieldType.LINK, now=builders.NOW)
    empty = FieldDefinition.create(name="Netto", data_type=FieldType.AMOUNT, now=builders.NOW)
    values = template_values(
        document,
        contact="Stadtwerke",
        document_type=None,
        space="Privat",
        owner="tobi",
        tags=["strom", "Gas"],
        document_date=date(2026, 3, 31),
        fields=[
            (amount, Money(Decimal("3650.00"), "EUR")),
            (link, Url("https://example.org/a")),
            (empty, None),
        ],
        contact_groups=["Versorger", "Arbeitgeber"],
    )
    assert values == {
        "title": document.title,
        "filename": "Scan 2026-03",
        "original_filename": "Scan 2026-03.pdf",
        "contact": "Stadtwerke",
        "document_date": date(2026, 3, 31),
        "added": builders.NOW.date(),
        "tags": ["Gas", "strom"],
        "contact_groups": ["Arbeitgeber", "Versorger"],
        "document_type_groups": [],
        "space": "Privat",
        "owner": "tobi",
        "channel": document.channel.value,
        "field": {"la_brutto": Money(Decimal("3650.00"), "EUR"), "portal": "https://example.org/a"},
    }
    # Not there, not None: a template tests it, writing it is a missing value.
    assert "document_type" not in values and "pages" not in values


def test_the_template_that_writes_each_value() -> None:
    values: dict[str, TemplateValue] = {
        "contact": "Stadtwerke",
        "tags": ["Strom", "Steuer"],
        "pages": 2,
        "field": {"la_brutto": Money(Decimal("3650.00"), "EUR"), "notiz": "x"},
    }
    assert writing_templates(values) == {
        "contact": "{{ contact }}",
        "tags": "{{ tags | join: ', ' }}",
        "pages": "{{ pages }}",
        "field.la_brutto": "{{ field.la_brutto }}",
        "field.notiz": "{{ field.notiz }}",
    }


def test_unknown_values() -> None:
    paths = {"contact", "field.la_brutto.amount", "field.brutto", "kontakt", "tags"}
    assert unknown_values(paths, {"la_brutto"}) == ["field.brutto", "kontakt"]
    assert unknown_values({"field"}, set()) == ["field"]
