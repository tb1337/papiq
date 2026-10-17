"""How master data looks: the icon of a tag or a document type, the colour of a tag, the logo
of a contact.

An icon is named after a Lucide icon (`house`, `heart-pulse`); the core checks only the form of
the name, a client shows a stand-in for one it does not know. A colour is one of the twelve of
the palette, which clients tune for light and dark themes, or one of the person's own as
`#rrggbb` (clients mix it with their background and text colours so that the text stays
readable).

A contact's logo is an image stored by the core at `objects.logo_key`: uploaded by an admin, or
fetched from the website at one of the contact's domains. An uploaded logo always wins: it is
never replaced by a fetched one. The domains whose fetch found nothing are noted, so that a
fetch is tried again only for another domain or when a person asks.
"""

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from papiq.core.domain.errors import ValidationError
from papiq.core.domain.validation import require_utc

MAX_ICON = 64
"""Characters of an icon's name."""

_ICON = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_HEX = re.compile(r"#[0-9a-f]{6}")


class PaletteColor(StrEnum):
    """The twelve colours of the palette, from grey around the colour wheel."""

    GRAY = "gray"
    RED = "red"
    ORANGE = "orange"
    AMBER = "amber"
    LIME = "lime"
    GREEN = "green"
    TEAL = "teal"
    SKY = "sky"
    BLUE = "blue"
    VIOLET = "violet"
    FUCHSIA = "fuchsia"
    PINK = "pink"


_PALETTE = tuple(color.value for color in PaletteColor)


def checked_icon(icon: str | None) -> str | None:
    """The name of an icon: lower-case letters and digits in words joined by hyphens, at most
    `MAX_ICON` characters; None or blank for none. ValidationError otherwise."""
    if icon is None or not icon.strip():
        return None
    icon = icon.strip()
    if len(icon) > MAX_ICON or not _ICON.fullmatch(icon):
        raise ValidationError(
            f"'{icon}' is no icon name (lower-case letters, digits and hyphens, at most "
            f"{MAX_ICON} characters)"
        )
    return icon


def checked_color(color: str | None) -> str | None:
    """A colour of the palette by its name, or an own colour as `#rrggbb` (in lower case);
    None or blank for none. ValidationError otherwise."""
    if color is None or not color.strip():
        return None
    value = color.strip().lower()
    if value in _PALETTE or _HEX.fullmatch(value):
        return value
    raise ValidationError(f"'{color}' is no colour: one of {', '.join(_PALETTE)} or #rrggbb")


class LogoSource(StrEnum):
    UPLOADED = "uploaded"  # by an admin: never replaced by a fetched one
    FETCHED = "fetched"  # from the website at one of the contact's domains


@dataclass(frozen=True, kw_only=True)
class ContactLogo:
    """A stored logo: where it came from, the domain of a fetched one, and when it was stored
    (also its version: a new logo is a new time)."""

    source: LogoSource
    stored_at: datetime
    domain: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "stored_at", require_utc(self.stored_at, "stored_at"))
        if (self.source is LogoSource.FETCHED) != (self.domain is not None):
            raise ValidationError("a fetched logo, and only one, names its domain")


@dataclass(frozen=True, kw_only=True)
class FailedLogoFetch:
    """Fetching a logo from the websites at `domains` found none, the last time at
    `failed_at`."""

    domains: frozenset[str]
    failed_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "failed_at", require_utc(self.failed_at, "failed_at"))
        if not self.domains:
            raise ValidationError("a failed fetch names its domains")
