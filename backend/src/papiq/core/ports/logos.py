"""Fetching the logo of a contact from its own website."""

from dataclasses import dataclass
from typing import Protocol

MIN_LOGO_SIZE = 64
"""Pixels (the longer side) an icon of a website needs to be a logo."""


@dataclass(frozen=True, kw_only=True)
class FetchedLogo:
    data: bytes  # the image as the website serves it (PNG, JPEG, WebP or ICO)
    url: str  # where it came from, for the log


class LogoFetcher(Protocol):
    """Asks a contact's website for its icon. First adapter: httpx2.

    The domain comes from the contact's identifiers, which training learns from documents: it
    is not trusted. Adapters go only over HTTPS to public addresses, follow few redirects and
    check each of them again, and bound every answer in size and time (see the adapter)."""

    async def fetch(self, domain: str) -> FetchedLogo:
        """The largest raster icon of at least `MIN_LOGO_SIZE` pixels the website at `domain`
        (or, if that cannot be reached, at `www.` before it) declares: its apple-touch-icon,
        the icons of `<link rel="icon">` and of its web app manifest, and `/favicon.ico`.
        LogoFetchError, with a short reason, if it declares none that fits or cannot be
        reached safely."""
        ...
