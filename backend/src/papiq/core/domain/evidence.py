"""Finding values in a document's text: the facts behind a classification's confidence.

A value counts as found only if the text shows it, in one of the usual ways of writing it:
dates in German, English and ISO notation, numbers with German or English separators,
currencies as code, symbol or word. Everything works on a normalised text (see `normalise`).
"""

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from functools import cached_property

# Markdown markup that may sit inside a value (`**84,20 €**`, table pipes).
_MARKUP = re.compile(r"[*_#|`>~]+")
# A word broken at the end of a line: `Rech-\nnung`.
_HYPHENATION = re.compile(r"(?<=[^\W\d_])-[ \t]*\n[ \t]*(?=[^\W\d_])")
_WHITESPACE = re.compile(r"\s+")
_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue"})


def normalise(text: str) -> str:
    """Compatibility forms unified (NFKC), lower case (case folding, so `ß` is `ss`),
    hyphenation at line ends and Markdown markup removed, whitespace collapsed."""
    text = unicodedata.normalize("NFKC", text).replace("­", "")
    text = _HYPHENATION.sub("", text)
    text = _MARKUP.sub(" ", text)
    return _WHITESPACE.sub(" ", text.casefold()).strip()


def fold_umlauts(text: str) -> str:
    """`ä` as `ae` and so on, for names written either way; expects normalised text."""
    return text.translate(_UMLAUTS)


# --- dates ------------------------------------------------------------------------------------

_MONTHS = {
    **dict.fromkeys(("januar", "jänner", "jaenner", "january", "jan"), 1),
    **dict.fromkeys(("februar", "february", "feb"), 2),
    **dict.fromkeys(("märz", "maerz", "march", "mär", "mrz", "mar"), 3),
    **dict.fromkeys(("april", "apr"), 4),
    **dict.fromkeys(("mai", "may"), 5),
    **dict.fromkeys(("juni", "june", "jun"), 6),
    **dict.fromkeys(("juli", "july", "jul"), 7),
    **dict.fromkeys(("august", "aug"), 8),
    **dict.fromkeys(("september", "sept", "sep"), 9),
    **dict.fromkeys(("oktober", "october", "okt", "oct"), 10),
    **dict.fromkeys(("november", "nov"), 11),
    **dict.fromkeys(("dezember", "december", "dez", "dec"), 12),
}
_MONTH = "(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\.?"
_DAY = r"(\d{1,2})(?:st|nd|rd|th|\.)?"
_DATE_PATTERNS: tuple[tuple[re.Pattern[str], tuple[str, str, str]], ...] = (
    # 31.03.2026, 31.3.26, 31/03/2026, 31-03-2026
    (re.compile(r"(?<![\d.])(\d{1,2})[./-](\d{1,2})[./-](\d{4}|\d{2})(?![\d])"), ("d", "m", "y")),
    # 2026-03-31, 2026/03/31
    (re.compile(r"(?<![\d.])(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?![\d])"), ("y", "m", "d")),
    # 31. März 2026, 31 Mar. 2026, 31st March 2026
    (re.compile(rf"(?<!\d){_DAY}\s*{_MONTH}\s*(\d{{4}})(?!\d)"), ("d", "m", "y")),
    # March 31, 2026; Mar 31st 2026
    (re.compile(rf"\b{_MONTH}\s*{_DAY},?\s*(\d{{4}})(?!\d)"), ("m", "d", "y")),
)


def dates_in(text: str) -> set[date]:
    """All dates written in the normalised `text`. Two-digit years: 00 to 69 are 20xx."""
    found: set[date] = set()
    for pattern, order in _DATE_PATTERNS:
        for match in pattern.finditer(text):
            parts = dict(zip(order, match.groups(), strict=True))
            month = int(parts["m"]) if parts["m"].isdigit() else _MONTHS[parts["m"]]
            year = int(parts["y"])
            if len(parts["y"]) == 2:
                year += 2000 if year < 70 else 1900
            try:
                found.add(date(year, month, int(parts["d"])))
            except ValueError:
                continue
    return found


# --- numbers ----------------------------------------------------------------------------------

# Digits with separators between them: `1.234,56`, `1,234.56`, `1 234,56`, `1'234.56`, `84,20`.
_NUMBER = re.compile(r"(?<![\d])\d(?:[\d]|[.,'](?=\d)| (?=\d{3}(?!\d)))*")


def numbers_in(text: str) -> set[Decimal]:
    """All numbers written in the normalised `text`, as absolute values. An ambiguous
    notation (`1.234`: thousand or one point two three four) yields both readings."""
    found: set[Decimal] = set()
    for match in _NUMBER.finditer(text):
        found.update(_readings(match.group()))
    return found


def _readings(token: str) -> set[Decimal]:
    readings: set[Decimal] = set()
    for decimal_mark, groups in ((",", ".' "), (".", ",' ")):
        number = _reading(token, decimal_mark, groups)
        if number is not None:
            readings.add(number)
    # Any prefix that ends before a space may be a number of its own (`Seite 1 234`).
    if " " in token:
        for part in token.split(" "):
            readings.update(_readings(part))
    return readings


def _reading(token: str, decimal_mark: str, groups: str) -> Decimal | None:
    """`token` read with `decimal_mark` and thousands grouped by one of `groups`; None if it
    does not fit that notation."""
    whole, mark, fraction = token.rpartition(decimal_mark)
    if not mark:
        whole = fraction
    elif decimal_mark in whole or not fraction.isdigit():
        return None
    parts = re.split(f"[{re.escape(groups)}]", whole)
    if len(parts) > 1:
        if not 1 <= len(parts[0]) <= 3 or any(len(part) != 3 for part in parts[1:]):
            return None
        if len({separator for separator in whole if separator in groups}) > 1:
            return None
    if not all(part.isdigit() for part in parts):
        return None
    try:
        return Decimal("".join(parts) + ("." + fraction if mark else ""))
    except InvalidOperation:
        return None


# --- currencies -------------------------------------------------------------------------------

_CURRENCY_SIGNS = {
    "€": {"EUR"},
    "euro": {"EUR"},
    "euros": {"EUR"},
    "$": {"USD"},
    "us$": {"USD"},
    "dollar": {"USD"},
    "dollars": {"USD"},
    "£": {"GBP"},
    "pfund": {"GBP"},
    "¥": {"JPY", "CNY"},
    "fr.": {"CHF"},
    "sfr.": {"CHF"},
    "sfr": {"CHF"},
    "franken": {"CHF"},
    "kč": {"CZK"},
    "zł": {"PLN"},
    "kr": {"SEK", "NOK", "DKK"},
    "kr.": {"SEK", "NOK", "DKK"},
}
# Active ISO 4217 codes; other three-letter words (BIS, DEN, SIE) are no currency.
_ISO_4217_CODES = """
    AED AFN ALL AMD ANG AOA ARS AUD AWG AZN BAM BBD BDT BGN BHD BIF BMD BND BOB BRL
    BSD BTN BWP BYN BZD CAD CDF CHF CLP CNY COP CRC CUP CVE CZK DJF DKK DOP DZD EGP
    ERN ETB EUR FJD FKP GBP GEL GHS GIP GMD GNF GTQ GYD HKD HNL HTG HUF IDR ILS INR
    IQD IRR ISK JMD JOD JPY KES KGS KHR KMF KPW KRW KWD KYD KZT LAK LBP LKR LRD LSL
    LYD MAD MDL MGA MKD MMK MNT MOP MRU MUR MVR MWK MXN MYR MZN NAD NGN NIO NOK NPR
    NZD OMR PAB PEN PGK PHP PKR PLN PYG QAR RON RSD RUB RWF SAR SBD SCR SDG SEK SGD
    SHP SLE SOS SRD SSP STN SVC SYP SZL THB TJS TMT TND TOP TRY TTD TWD TZS UAH UGX
    USD UYU UZS VES VND VUV WST XAF XCD XCG XOF XPF YER ZAR ZMW ZWG
"""
_ISO_4217 = frozenset(_ISO_4217_CODES.split())
_WORD = re.compile(r"[^\W\d_]+\.?|[€$£¥]")


def currencies_in(text: str) -> set[str]:
    """ISO 4217 codes the normalised `text` shows: as code (`EUR`), sign (`€`) or word
    (`Euro`)."""
    found: set[str] = set()
    for match in _WORD.finditer(text):
        word = match.group()
        found.update(_CURRENCY_SIGNS.get(word, ()))
        bare = word.rstrip(".")
        found.update(_CURRENCY_SIGNS.get(bare, ()))
        if bare.upper() in _ISO_4217:
            found.add(bare.upper())
    if "us$" in text:
        found.add("USD")
    return found


# --- text -------------------------------------------------------------------------------------

_SHORTEST_SPACELESS = 8  # characters; shorter values compared without spaces match too easily


def contains(text: str, value: str) -> bool:
    """Whether the normalised `text` contains `value` (normalised here) as whole words: as is,
    with umlauts written as `ae`/`oe`/`ue`, or, for longer values, ignoring spaces (an IBAN in
    groups). `Z-3` is not in `Z-3141`."""
    needle = normalise(value)
    if not needle:
        return False
    if _whole(needle, text) or _whole(fold_umlauts(needle), fold_umlauts(text)):
        return True
    spaceless = needle.replace(" ", "")
    return len(spaceless) >= _SHORTEST_SPACELESS and spaceless in text.replace(" ", "")


def _whole(needle: str, text: str) -> bool:
    """`needle` occurs in `text` as whole words: not next to a letter or digit, nor joined to
    one by `-`, `.`, `/` or `,` (`R-2026` is not in `R-2026-0815`, `31` not in `31.03.2026`)."""
    pattern = r"(?<![^\W_])(?<![^\W_][-./,])" + re.escape(needle) + r"(?![^\W_])(?![-./,][^\W_])"
    return re.search(pattern, text) is not None


@dataclass(frozen=True)
class DocumentText:
    """A document's text with what has been found in it, computed once."""

    raw: str

    @cached_property
    def normalised(self) -> str:
        return normalise(self.raw)

    @cached_property
    def dates(self) -> set[date]:
        return dates_in(self.normalised)

    @cached_property
    def numbers(self) -> set[Decimal]:
        return numbers_in(self.normalised)

    @cached_property
    def currencies(self) -> set[str]:
        return currencies_in(self.normalised)

    def contains(self, value: str) -> bool:
        return contains(self.normalised, value)

    def has_date(self, value: date) -> bool:
        return value in self.dates

    def has_number(self, value: Decimal) -> bool:
        return abs(value) in self.numbers

    def has_currency(self, code: str) -> bool:
        return code in self.currencies
