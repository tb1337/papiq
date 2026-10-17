"""Icons, colours and logos: the forms the core accepts."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from papiq.core.domain.appearance import (
    MAX_ICON,
    ContactLogo,
    FailedLogoFetch,
    LogoSource,
    PaletteColor,
    checked_color,
    checked_icon,
)
from papiq.core.domain.errors import ValidationError
from papiq.core.domain.identifiers import Identifier, IdentifierKind, IdentifierSource
from papiq.core.domain.master_data import Contact, DocumentType, Tag, TagScope

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("given", "kept"),
    [("house", "house"), (" heart-pulse ", "heart-pulse"), ("gamepad-2", "gamepad-2"),
     ("x" * MAX_ICON, "x" * MAX_ICON), (None, None), ("", None), ("  ", None)],
)  # fmt: skip
def test_an_icon_is_a_name_of_lower_case_words(given: str | None, kept: str | None) -> None:
    assert checked_icon(given) == kept


@pytest.mark.parametrize(
    "given",
    ["House", "heart_pulse", "-house", "house-", "heart--pulse", "x" * (MAX_ICON + 1), "a b",
     "../house", "häuser"],
)  # fmt: skip
def test_other_icon_names_are_refused(given: str) -> None:
    with pytest.raises(ValidationError):
        checked_icon(given)


def test_a_colour_is_one_of_the_palette_or_hex() -> None:
    assert len(PaletteColor) == 12
    assert [checked_color(color) for color in PaletteColor] == list(PaletteColor)
    assert checked_color(" Teal ") == "teal"
    assert checked_color("#B45309") == "#b45309"
    assert checked_color(None) is None and checked_color("") is None
    for wrong in ("purple", "#b4530", "#b453091", "b45309", "#gggggg", "rgb(1,2,3)"):
        with pytest.raises(ValidationError):
            checked_color(wrong)


def test_tags_and_types_check_their_looks() -> None:
    tag = Tag.create(name="Health", now=NOW, icon="heart-pulse", color="#AABBCC")
    assert (tag.icon, tag.color) == ("heart-pulse", "#aabbcc")
    tag.set_icon(None)
    tag.set_color("pink")
    assert (tag.icon, tag.color) == (None, "pink")
    group = Tag.create(name="Insurer", now=NOW, scope=TagScope.CONTACT, icon="shield")
    assert group.icon == "shield"
    with pytest.raises(ValidationError):
        tag.set_color("lila")
    with pytest.raises(ValidationError):
        Tag.create(name="X", now=NOW, icon="Heart")
    kind = DocumentType.create(name="Invoice", now=NOW, icon="receipt")
    kind.set_icon("receipt-euro")
    assert kind.icon == "receipt-euro"
    with pytest.raises(ValidationError):
        kind.set_icon("receipt euro")


def test_a_logo_names_where_it_came_from() -> None:
    uploaded = ContactLogo(source=LogoSource.UPLOADED, stored_at=NOW)
    fetched = ContactLogo(source=LogoSource.FETCHED, stored_at=NOW, domain="nord.example")
    assert (uploaded.domain, fetched.domain) == (None, "nord.example")
    with pytest.raises(ValidationError):
        ContactLogo(source=LogoSource.FETCHED, stored_at=NOW)
    with pytest.raises(ValidationError):
        ContactLogo(source=LogoSource.UPLOADED, stored_at=NOW, domain="nord.example")
    with pytest.raises(ValidationError):
        ContactLogo(source=LogoSource.UPLOADED, stored_at=NOW.replace(tzinfo=None))
    offset = timezone(timedelta(hours=2))
    with pytest.raises(ValidationError):
        FailedLogoFetch(domains=frozenset({"nord.example"}), failed_at=NOW.astimezone(offset))
    with pytest.raises(ValidationError):
        FailedLogoFetch(domains=frozenset(), failed_at=NOW)


def test_a_contact_keeps_its_logo_and_the_domains_whose_fetch_failed() -> None:
    contact = Contact.create(
        name="Nord",
        now=NOW,
        identifiers=[
            Identifier(IdentifierKind.EMAIL_DOMAIN, "nord.example"),
            Identifier(IdentifierKind.IBAN, "DE89370400440532013000"),
        ],
    )
    contact.set_learned([Identifier(IdentifierKind.EMAIL_DOMAIN, "a-nord.example")])
    contact.set_identifiers(
        [Identifier(IdentifierKind.EMAIL_DOMAIN, "nord.example")],
        rejected=[Identifier(IdentifierKind.EMAIL_DOMAIN, "old-nord.example")],
    )
    # Entered first, then learned; a rejected one is no website of the contact.
    assert contact.domains == ["nord.example", "a-nord.example"]
    assert contact.identifiers[-1].source is IdentifierSource.REJECTED

    contact.note_failed_fetch(["nord.example"], NOW)
    later = NOW + timedelta(hours=1)
    contact.note_failed_fetch(["a-nord.example", "nord.example"], later)
    both = FailedLogoFetch(domains=frozenset({"nord.example", "a-nord.example"}), failed_at=later)
    assert (contact.logo, contact.logo_failure) == (None, both)
    assert contact.fetch_failed("nord.example") and not contact.fetch_failed("x.example")
    logo = ContactLogo(source=LogoSource.FETCHED, stored_at=NOW, domain="a-nord.example")
    contact.set_logo(logo)
    assert (contact.logo, contact.logo_failure) == (logo, None)
    contact.note_failed_fetch(["nord.example"], NOW)
    assert (contact.logo, contact.fetch_failed("nord.example")) == (logo, True)
    contact.remove_logo()
    assert (contact.logo, contact.logo_failure) == (None, None)
