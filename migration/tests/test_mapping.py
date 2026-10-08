from collections.abc import Sequence
from typing import Any

import pytest

from papiq_migration.mapping import (
    access_of,
    convert_value,
    document_date,
    document_title,
    drawer_name,
    field_spec,
    notes_text,
)


def field(kind: str, **extra: Any) -> dict[str, Any]:
    return {"id": 1, "name": "F", "data_type": kind, "extra_data": extra}


@pytest.mark.parametrize(
    ("kind", "raw", "value"),
    [
        ("string", " Hello ", "Hello"),
        ("longtext", "Line\nLine", "Line\nLine"),
        ("url", "https://example.org/a?b=1", "https://example.org/a?b=1"),
        ("date", "2024-06-30", "2024-06-30"),
        ("date", "2024-06-30T10:00:00Z", "2024-06-30"),
        ("boolean", False, False),
        ("integer", 42, "42"),
        ("float", 1.5, "1.5"),
        ("float", 123.4567, "123.4567"),
        ("monetary", "EUR12.50", {"amount": "12.50", "currency": "EUR"}),
        ("monetary", "usd 3", {"amount": "3", "currency": "USD"}),
        ("monetary", "5914.00", {"amount": "5914.00", "currency": "CHF"}),
        ("monetary", "-5", {"amount": "-5", "currency": "CHF"}),
    ],
)
def test_values_are_converted(kind: str, raw: Any, value: Any) -> None:
    assert convert_value(field(kind), raw, "CHF").value == value


def test_a_monetary_field_uses_its_default_currency() -> None:
    converted = convert_value(field("monetary", default_currency="USD"), "10.00", "EUR")
    assert converted.value == {"amount": "10.00", "currency": "USD"}


def test_select_values_become_the_option_label() -> None:
    select = field(
        "select", select_options=[{"id": "a1", "label": "One"}, {"id": "b2", "label": " Two "}]
    )
    assert convert_value(select, "b2", "EUR").value == "Two"
    assert "does not exist" in (convert_value(select, "zz", "EUR").note or "")


@pytest.mark.parametrize(
    ("kind", "raw"),
    [
        ("url", "ftp://example.org"),
        ("url", "example.org"),
        ("url", "https://exa mple.org"),
        ("date", "31.12.2024"),
        ("boolean", "yes"),
        ("integer", "many"),
        ("monetary", "twelve"),
        ("documentlink", [1, 2]),
    ],
)
def test_values_that_do_not_fit_are_left_out_with_a_reason(kind: str, raw: Any) -> None:
    converted = convert_value(field(kind), raw, "EUR")
    assert converted.value is None and converted.note


@pytest.mark.parametrize("raw", [None, "", "  ", []])
def test_empty_values_are_left_out(raw: Any) -> None:
    assert convert_value(field("string"), raw, "EUR").value is None


def test_every_field_type_has_an_attribute_type_except_document_links() -> None:
    expected = {
        "string": "text",
        "longtext": "text",
        "url": "link",
        "date": "date",
        "boolean": "boolean",
        "integer": "number",
        "float": "number",
        "monetary": "amount",
        "select": "choice",
    }
    for kind, data_type in expected.items():
        extra = {"select_options": [{"id": "a", "label": "A"}]} if kind == "select" else {}
        spec, _ = field_spec(field(kind, **extra))
        assert spec is not None and spec.data_type == data_type
    spec, notes = field_spec(field("documentlink"))
    assert spec is None and "no counterpart" in notes[0]


def test_options_with_the_same_label_are_merged() -> None:
    options = [{"id": "a", "label": "A"}, {"id": "b", "label": "A"}, {"id": "c", "label": "C"}]
    spec, notes = field_spec(field("select", select_options=options))
    assert spec is not None and spec.choices == ("A", "C") and notes


def test_titles_and_dates() -> None:
    assert document_title({"id": 1, "title": "  Bill  "}) == ("Bill", [])
    title, notes = document_title({"id": 1, "title": "", "original_file_name": "scan.pdf"})
    assert (title, len(notes)) == ("scan", 1)
    title, notes = document_title({"id": 7, "title": "", "original_file_name": ""})
    assert title == "Document 7"
    title, notes = document_title({"id": 1, "title": "x" * 600})
    assert len(title) == 500 and notes
    assert document_date({"created": "2024-05-01"}) == "2024-05-01"
    assert document_date({"created": "2024-05-01T10:00:00+02:00"}) == "2024-05-01"
    assert document_date({"created": None}) is None


def test_notes_become_one_text_oldest_first() -> None:
    text, notes = notes_text(
        [
            {"note": "Second", "created": "2024-02-02T10:00:00Z", "user": {"username": "anna"}},
            {"note": "First", "created": "2024-01-01T10:00:00Z", "user": {"username": "tobias"}},
            {"note": "  ", "created": "2024-03-03T10:00:00Z", "user": None},
        ]
    )
    assert text == "2024-01-01, tobias: First\n\n2024-02-02, anna: Second" and notes == []
    assert notes_text([]) == (None, [])
    long, notes = notes_text([{"note": "x" * 30_000, "created": "2024-01-01", "user": None}])
    assert long is not None and len(long) <= 20_000 and notes


USERS = {
    1: {"id": 1, "username": "tobias", "is_active": True, "groups": [1]},
    2: {"id": 2, "username": "anna", "is_active": True, "groups": [1]},
    3: {"id": 3, "username": "bob", "is_active": True, "groups": []},
    4: {"id": 4, "username": "gone", "is_active": False, "groups": []},
}


def permissions(
    view_users: Sequence[int] = (),
    view_groups: Sequence[int] = (),
    change_users: Sequence[int] = (),
    change_groups: Sequence[int] = (),
) -> dict[str, Any]:
    return {
        "view": {"users": list(view_users), "groups": list(view_groups)},
        "change": {"users": list(change_users), "groups": list(change_groups)},
    }


def test_no_permissions_means_no_shares() -> None:
    access = access_of({"owner": 1, "permissions": permissions()}, USERS)
    assert (access.owner, access.shares, access.notes) == ("tobias", (), ())


def test_groups_are_resolved_and_writers_win_over_readers() -> None:
    access = access_of(
        {"owner": 1, "permissions": permissions([3], [1], [2], [])},
        USERS,
    )
    # tobias (the owner) is left out; anna reads via the group and writes directly; bob reads.
    assert access.shares == (("anna", "read_write"), ("bob", "read"))
    access = access_of({"owner": 3, "permissions": permissions([], [], [], [1])}, USERS)
    assert access.shares == (("anna", "read_write"), ("tobias", "read_write"))


def test_no_owner_unknown_owner_and_unusable_users_are_noted() -> None:
    nobody = access_of({"owner": None, "permissions": permissions([2])}, USERS)
    assert nobody.owner is None and nobody.shares == (("anna", "read"),)
    assert "no owner" in nobody.notes[0]
    deleted = access_of({"owner": 99, "permissions": permissions()}, USERS)
    assert deleted.owner is None and "no longer exists" in deleted.notes[0]
    unusable = access_of({"owner": 1, "permissions": permissions([4, 77])}, USERS)
    assert unusable.shares == () and len(unusable.notes) == 2


def test_missing_permission_data_is_no_permission() -> None:
    assert access_of({"owner": 1}, USERS).shares == ()


def test_drawer_names_name_the_users_and_stay_in_bounds() -> None:
    assert drawer_name((("anna", "read_write"), ("bob", "read"))) == (
        "Geteilt: anna (schreiben), bob (lesen)"
    )
    many = tuple((f"user-{n:03d}-" + "x" * 20, "read") for n in range(30))
    name = drawer_name(many)
    assert len(name) <= 200 and name != drawer_name(many[:-1])
    assert drawer_name(many) == name
