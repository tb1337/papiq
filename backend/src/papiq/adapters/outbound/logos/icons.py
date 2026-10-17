"""The icons a website declares, read with the standard library's `html.parser`: in its page
(`<link rel="apple-touch-icon">`, `<link rel="icon">` with `sizes`, the web app manifest's
`<link rel="manifest">`, `<base href>`) and in its manifest (`icons`), and `/favicon.ico`.

Only raster images over HTTPS count: an SVG (by its type or name), a mask icon, a monochrome
manifest icon and anything that is not `https:` once resolved are left out, and so is an icon
declared smaller than the least a logo needs. The rest are ranked by the size they declare,
largest first (an apple-touch-icon without sizes is 180 px by Apple's convention); those
without a size follow, `/favicon.ico` last.
"""

import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from papiq.core.ports.logos import MIN_LOGO_SIZE

APPLE_TOUCH_SIZE = 180
"""Pixels an apple-touch-icon has when it does not say."""
_ICON_RELS = {"icon", "apple-touch-icon", "apple-touch-icon-precomposed"}
_MAX_CANDIDATES = 50


@dataclass(frozen=True)
class Candidate:
    url: str
    size: int | None  # declared, the longer side in pixels; None if not declared
    rank: int = 0  # among equal sizes, lower first (a maskable manifest icon after others)


_SIZE = re.compile(r"([0-9]{1,5})x([0-9]{1,5})")


def declared_size(sizes: str | None) -> int | None:
    """The longest side among `sizes` ("16x16 32x32"); None for none or "any"."""
    best = None
    for item in (sizes or "").lower().split():
        match = _SIZE.fullmatch(item)
        if match:
            best = max(best or 0, int(match[1]), int(match[2]))
    return best


class _Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.base: str | None = None
        self.links: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name.lower(): value or "" for name, value in attrs}
        if tag == "base" and self.base is None and values.get("href"):
            self.base = values["href"]
        elif tag == "link" and len(self.links) < _MAX_CANDIDATES and values.get("href"):
            self.links.append(values)


def page_icons(html: str, page_url: str) -> tuple[list[Candidate], str | None]:
    """The icons the page at `page_url` declares, and the URL of its manifest, if any."""
    parser = _Page()
    try:
        parser.feed(html)
        parser.close()
    except (ValueError, AssertionError):  # what a broken page may make the parser raise
        pass
    base = urljoin(page_url, parser.base) if parser.base else page_url
    icons: list[Candidate] = []
    manifest: str | None = None
    for link in parser.links:
        rels = set(link.get("rel", "").lower().split())
        url = _https(base, link["href"])
        if url is None:
            continue
        if "manifest" in rels and manifest is None:
            manifest = url
        if not rels & _ICON_RELS or "mask-icon" in rels or _vector(url, link.get("type")):
            continue
        size = declared_size(link.get("sizes"))
        if size is None and rels & {"apple-touch-icon", "apple-touch-icon-precomposed"}:
            size = APPLE_TOUCH_SIZE
        icons.append(Candidate(url, size))
    return icons, manifest


def manifest_icons(text: str, manifest_url: str) -> list[Candidate]:
    """The icons of a web app manifest (JSON) at `manifest_url`."""
    try:
        manifest = json.loads(text)
    except (ValueError, RecursionError):  # nested deeper than the parser goes
        return []
    found = manifest.get("icons") if isinstance(manifest, dict) else None
    if not isinstance(found, list):
        return []
    icons = []
    for item in found[:_MAX_CANDIDATES]:
        if not isinstance(item, dict) or not isinstance(item.get("src"), str):
            continue
        purposes = str(item.get("purpose") or "any").lower().split()
        url = _https(manifest_url, item["src"])
        kind = item.get("type") if isinstance(item.get("type"), str) else None
        if url is None or _vector(url, kind) or purposes == ["monochrome"]:
            continue
        sizes = item.get("sizes") if isinstance(item.get("sizes"), str) else None
        rank = 0 if "any" in purposes else 1
        icons.append(Candidate(url, declared_size(sizes), rank))
    return icons


def ranked(candidates: list[Candidate], favicon: str) -> list[str]:
    """The URLs to try, best first: declared sizes from the largest down (those too small left
    out), then those without a size, `favicon` last; each once."""
    sized = [item for item in candidates if item.size is not None and item.size >= MIN_LOGO_SIZE]
    unsized = [item for item in candidates if item.size is None]
    order = sorted(sized, key=lambda item: (-(item.size or 0), item.rank))
    urls = [item.url for item in [*order, *sorted(unsized, key=lambda item: item.rank)]]
    return list(dict.fromkeys([*urls, favicon]))


def _https(base: str, href: str) -> str | None:
    try:
        url = urljoin(base, href.strip())
        parts = urlsplit(url)
    except ValueError:
        return None
    return url if parts.scheme == "https" and parts.hostname else None


def _vector(url: str, kind: str | None) -> bool:
    return "svg" in (kind or "").lower() or urlsplit(url).path.lower().endswith((".svg", ".svgz"))
