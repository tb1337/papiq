"""What a document shows of its contact: names of contacts and identifiers, in its text and in
the codes on its pages, each with where it stands in the text.

The facts signal reports them for the judge (`signal_facts`); a person sees them beside the
text. A name counts whole (`names.mentions`); in a code only the payee of a GiroCode is a name.
A found identifier is matched with the contacts' (`identifiers.matches`): one that counts
(entered or learned) is evidence for its contact, and for its topic if it points to one; a
rejected one is known, but neither evidence nor unknown.

Where it stands: character spans of the text (`spans`), found with the written forms a value
has (an IBAN in groups of four, a customer number with dashes, a name with any spaces and
punctuation between its words, umlauts or not). A name broken over two columns has none.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import islice

from papiq.core.domain.codes import Code, code_identifiers, giro_code
from papiq.core.domain.identifiers import IdentifierKind, identifiers_in, matches
from papiq.core.domain.ids import ContactId, TagId
from papiq.core.domain.master_data import Contact
from papiq.core.domain.names import mentions, name_key

MOST = 20
"""Names and identifiers reported, each."""
_SPANS = 20  # places of one fact in the text
_UMLAUTS = {"ae": "(?:ä|ae)", "oe": "(?:ö|oe)", "ue": "(?:ü|ue)", "ss": "(?:ß|ss)"}

type Key = tuple[IdentifierKind, str]


@dataclass(frozen=True)
class Span:
    """Characters `start` to `end` (exclusive) of the text."""

    start: int
    end: int


@dataclass(frozen=True, kw_only=True)
class NamedContact:
    contact_id: ContactId
    name: str  # the name or alias as the contact has it
    alias: bool
    in_text: bool
    in_code: bool  # the payee of a GiroCode
    spans: tuple[Span, ...] = ()


@dataclass(frozen=True, kw_only=True)
class FoundIdentifier:
    kind: IdentifierKind
    value: str  # normalised
    contact_id: ContactId | None  # the contact it is an identifier of, if one that counts
    in_text: bool
    in_code: bool
    spans: tuple[Span, ...] = ()
    topic_id: TagId | None = None  # the contact's topic it points to, if any

    @property
    def key(self) -> Key:
        return (self.kind, self.value)


@dataclass(frozen=True, kw_only=True)
class Facts:
    named: tuple[NamedContact, ...]
    identifiers: tuple[FoundIdentifier, ...]  # those of contacts first, then the unknown


def facts_in(
    text: str, contacts: Sequence[Contact], codes: Sequence[Code] = (), *, places: bool = False
) -> Facts:
    """The facts of `text` and of the `codes` on its pages; with `places`, their spans."""
    text_key = name_key(text)
    payees = [name_key(giro.name) for code in codes if (giro := giro_code(code.text))]
    named: list[NamedContact] = []
    for contact in contacts:
        own = next((name for name in contact.names() if mentions(text_key, name_key(name))), None)
        paid = next(
            (n for n in contact.names() if any(mentions(p, name_key(n)) for p in payees)), None
        )
        name = own if own is not None else paid
        if name is None:
            continue
        named.append(
            NamedContact(
                contact_id=contact.id,
                name=name,
                alias=name != contact.name,
                in_text=own is not None,
                in_code=paid is not None,
                spans=name_spans(text, name) if places and own is not None else (),
            )
        )
    in_text = identifiers_in(text)
    in_codes: set[Key] = set()
    for code in codes:
        in_codes |= code_identifiers(code)
    owners: dict[Key, ContactId] = {}
    topics: dict[Key, TagId | None] = {}
    rejected: set[Key] = set()
    for contact in contacts:
        for identifier in contact.identifiers:
            for key in in_text | in_codes:
                if matches(identifier, key):
                    if identifier.counts:
                        if key not in owners:
                            owners[key] = contact.id
                            topics[key] = identifier.topic_id
                    else:
                        rejected.add(key)
    found = [
        FoundIdentifier(
            kind=kind,
            value=value,
            contact_id=owners.get((kind, value)),
            in_text=(kind, value) in in_text,
            in_code=(kind, value) in in_codes,
            spans=identifier_spans(text, kind, value) if places else (),
            topic_id=topics.get((kind, value)),
        )
        for kind, value in sorted(in_text | in_codes, key=lambda key: (key[0].value, key[1]))
        if (kind, value) in owners or (kind, value) not in rejected
    ]
    known = [item for item in found if item.contact_id is not None]
    unknown = [item for item in found if item.contact_id is None]
    return Facts(named=tuple(named[:MOST]), identifiers=(*known[:MOST], *unknown[:MOST]))


def name_spans(text: str, name: str) -> tuple[Span, ...]:
    """Where a name stands in `text`: its words in order, anything but letters and digits
    between them, umlauts or their spellings; legal forms aside, as names are compared."""
    words = name_key(name).split()
    if not words:
        return ()
    pattern = r"[\W_]+".join(_spelling(word) for word in words)
    return _spans(rf"(?<!\w){pattern}(?!\w)", text)


def identifier_spans(text: str, kind: IdentifierKind, value: str) -> tuple[Span, ...]:
    """Where an identifier stands in `text`, as it may be written: separators between its
    characters (an IBAN, a VAT, customer or personnel number), any case; a personnel number
    also with leading zeros."""
    if kind is IdentifierKind.EMAIL_DOMAIN:
        return _spans(rf"(?<![\w-]){re.escape(value)}(?![\w-])", text)
    pattern = r"[\s./-]?".join(re.escape(char) for char in value)
    if kind is IdentifierKind.PERSONNEL_NUMBER:
        pattern = rf"(?:0[\s./-]?)*{pattern}"
    return _spans(rf"(?<![A-Za-z0-9]){pattern}(?![A-Za-z0-9])", text)


def _spelling(word: str) -> str:
    pattern = re.escape(word)
    for folded, written in _UMLAUTS.items():
        pattern = pattern.replace(folded, written)
    return pattern


def _spans(pattern: str, text: str) -> tuple[Span, ...]:
    found = re.finditer(pattern, text, re.IGNORECASE)
    return tuple(Span(match.start(), match.end()) for match in islice(found, _SPANS))
