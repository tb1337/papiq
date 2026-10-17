"""Identifiers of contacts: values a document of the contact carries and no other contact's does
(an IBAN, a VAT number, the customer number, the personnel number at an employer, the domain of
its e-mail addresses). Found in the text, they are hard evidence for the contact.

Admins enter identifiers by hand. Training learns more from confirmed documents (see
`mine_identifiers`): a value that stands in at least two of them, all of one contact. A value
in documents of several contacts (the owner's own IBAN on every direct debit) is never learned.
An admin can reject a learned one; it is then not learned again.

An identifier may point to one of its contact's topics (a personnel number to "work", a policy
number to "health insurance"): found in a document, it is evidence for the pair of contact and
topic. A learned one gets a topic only if all the confirmed documents it stands in carry it.
"""

import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum

from papiq.core.domain.errors import ValidationError
from papiq.core.domain.evidence import normalise
from papiq.core.domain.ids import ContactId, TagId

MAX_IDENTIFIERS = 100
"""Identifiers of one contact, entered and learned."""
LEARN_FROM = 2
"""Confirmed documents of one contact a value must stand in to be learned."""


class IdentifierKind(StrEnum):
    IBAN = "iban"
    VAT_ID = "vat_id"
    CUSTOMER_NUMBER = "customer_number"  # also contract, policy and membership numbers
    PERSONNEL_NUMBER = "personnel_number"  # an employee's number at the employer
    EMAIL_DOMAIN = "email_domain"  # of e-mail addresses and web addresses


class IdentifierSource(StrEnum):
    MANUAL = "manual"
    LEARNED = "learned"
    REJECTED = "rejected"  # learned, then rejected by an admin: not learned again


@dataclass(frozen=True)
class Identifier:
    """`value` is normalised (see `normalise_identifier`); kind and value are unique across
    all contacts. `topic_id`: the topic of its contact it points to, if any (the contact checks
    that it enables it)."""

    kind: IdentifierKind
    value: str
    source: IdentifierSource = IdentifierSource.MANUAL
    topic_id: TagId | None = None

    @classmethod
    def parse(
        cls,
        kind: IdentifierKind,
        raw: str,
        source: IdentifierSource = IdentifierSource.MANUAL,
        topic_id: TagId | None = None,
    ) -> "Identifier":
        return cls(kind, normalise_identifier(kind, raw), source, topic_id)

    @property
    def key(self) -> tuple[IdentifierKind, str]:
        return self.kind, self.value

    @property
    def counts(self) -> bool:
        """Whether it is evidence (a rejected one only keeps training from learning it)."""
        return self.source is not IdentifierSource.REJECTED


# --- normalising ------------------------------------------------------------------------------

_IBAN = re.compile(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}")
_VAT_ID = re.compile(r"[A-Z]{2,3}(?=[A-Z0-9]*\d{6})[A-Z0-9]{8,12}")
_CUSTOMER_NUMBER = re.compile(r"(?=[A-Z0-9]*\d)[A-Z0-9]{4,30}")
_PERSONNEL_NUMBER = re.compile(r"(?=[A-Z0-9]*\d)[A-Z0-9]{3,30}")
_DOMAIN = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}")
_SEPARATORS = re.compile(r"[\s./-]+")

FREE_MAIL = frozenset(
    {
        *("aol.com", "arcor.de", "bluewin.ch", "freenet.de", "gmail.com", "googlemail.com"),
        *("gmx.at", "gmx.ch", "gmx.de", "gmx.net", "hotmail.com", "hotmail.de", "icloud.com"),
        *("live.com", "live.de", "mail.de", "mailbox.org", "me.com", "msn.com", "online.de"),
        *("outlook.com", "outlook.de", "posteo.de", "proton.me", "protonmail.com"),
        *("t-online.de", "web.de", "yahoo.com", "yahoo.de"),
    }
)
"""Domains of free mail providers: their addresses belong to people, not to one contact."""


def normalise_identifier(kind: IdentifierKind, raw: str) -> str:
    """The comparable form of an identifier: IBAN, VAT, customer and personnel numbers without
    spaces and separators in upper case (an IBAN with a valid check sum, a personnel number
    without leading zeros), a domain in lower case without `www.`; ValidationError if `raw` is
    none of its kind."""
    text = raw.strip()
    match kind:
        case IdentifierKind.IBAN:
            value = re.sub(r"\s+", "", text).upper()
            if not _IBAN.fullmatch(value) or not iban_valid(value):
                raise ValidationError(f"'{raw}' is not a valid IBAN")
        case IdentifierKind.VAT_ID:
            value = _SEPARATORS.sub("", text).upper()
            if not _VAT_ID.fullmatch(value):
                raise ValidationError(f"'{raw}' is not a VAT number")
        case IdentifierKind.CUSTOMER_NUMBER:
            value = _SEPARATORS.sub("", text).upper()
            if not _CUSTOMER_NUMBER.fullmatch(value):
                raise ValidationError(
                    f"'{raw}' is no customer number (4 to 30 letters and digits, one a digit)"
                )
        case IdentifierKind.PERSONNEL_NUMBER:
            # Written with or without leading zeros (`00012345`, `12345`): one number.
            written = _SEPARATORS.sub("", text).upper()
            value = written.lstrip("0")
            if not _PERSONNEL_NUMBER.fullmatch(written) or not value:
                raise ValidationError(
                    f"'{raw}' is no personnel number (3 to 30 letters and digits, one a digit)"
                )
        case IdentifierKind.EMAIL_DOMAIN:
            value = text.lower().removeprefix("mailto:").rpartition("@")[2]
            value = re.sub(r"^(?:https?://)?(?:www\.)?", "", value).split("/")[0].rstrip(".")
            if not _DOMAIN.fullmatch(value):
                raise ValidationError(f"'{raw}' is not a domain")
            if value in FREE_MAIL:
                raise ValidationError(f"'{value}' is a free mail provider, not one contact's")
    return value


def iban_valid(iban: str) -> bool:
    """The ISO 13616 check sum (mod 97) of a compact IBAN in upper case."""
    rearranged = iban[4:] + iban[:4]
    digits = "".join(str(int(char, 36)) for char in rearranged)
    return int(digits) % 97 == 1


# --- finding in a text ------------------------------------------------------------------------

# Groups of four, the country also apart from its check digits (`DE 89 3704 …`).
_IBAN_IN_TEXT = re.compile(
    r"(?<![a-z0-9])[a-z]{2} ?\d{2}(?: ?[a-z0-9]{4}){2,7}(?: ?[a-z0-9]{1,4})?"
)
_SHORTEST_IBAN = 15  # characters (Norway)
_GERMAN_VAT = re.compile(r"(?<![a-z0-9])de ?\d{3} ?\d{3} ?\d{3}(?![a-z0-9])")
# A label, then the value: its first word, and further words that hold a digit (`DE 123 456
# 789`, `123 456`), not the words after it (`DE123456789 Steuernummer`).
_VALUE = r"\.?\s*[:.]?\s*([a-z0-9][a-z0-9./-]*(?: (?=[a-z./-]*\d)[a-z0-9./-]+)*)"
_VAT_LABEL = re.compile(
    r"(?<![a-z])(?:ust[-.\s]*id(?:[-.\s]*nr)?|umsatzsteuer[-\s]*id(?:entifikations)?[-\s]*"
    r"(?:nummer|nr)|vat[-\s]*(?:id|no|number|reg(?:istration)?(?:[-\s]*no)?)?"
    r"|uid[-\s]*(?:nr|nummer)?)" + _VALUE
)
_CUSTOMER_LABEL = re.compile(
    r"(?:kunden[-\s]*(?:nummer|nr|konto)|kd[-.\s]*nr|kundennr|vertrags[-\s]*(?:nummer|nr|konto)"
    r"|versicherungs[-\s]*(?:nummer|nr|schein[-\s]*(?:nummer|nr))|mitglieds[-\s]*(?:nummer|nr)"
    r"|customer[-\s]*(?:no|number|id)|account[-\s]*(?:no|number))" + _VALUE
)
_PERSONNEL_LABEL = re.compile(
    r"(?<![a-z])(?:personal[-\s]*(?:nummer|nr)|pers[-.\s]*nr|mitarbeiter[-\s]*(?:nummer|nr)"
    r"|employee[-\s]*(?:no|number|id)|personnel[-\s]*(?:no|number))" + _VALUE
)
_EMAIL = re.compile(r"[a-z0-9._%+-]+@((?:[a-z0-9-]+\.)+[a-z]{2,63})")
_WEB = re.compile(r"(?<![a-z0-9@.-])(?:https?://(?:www\.)?|www\.)((?:[a-z0-9-]+\.)+[a-z]{2,63})")


def identifiers_in(text: str) -> set[tuple[IdentifierKind, str]]:
    """The identifiers `text` shows, normalised: IBANs and German VAT numbers anywhere, other
    VAT numbers, customer and personnel numbers after their label ("USt-IdNr.", "Kundennummer",
    "Vertragsnummer", "Personalnummer", ...), the domains of e-mail and web addresses (free
    mail aside)."""
    normalised = normalise(text)
    found: set[tuple[IdentifierKind, str]] = set()

    def add(kind: IdentifierKind, raw: str) -> None:
        with suppress(ValidationError):
            found.add((kind, normalise_identifier(kind, raw)))

    for match in _IBAN_IN_TEXT.finditer(normalised):
        # The groups may run into what follows (`DE89 3704 … 3000 Sparkasse`): the longest
        # start with a valid check sum is the IBAN.
        compact = match.group().replace(" ", "")
        for end in range(len(compact), _SHORTEST_IBAN - 1, -1):
            if iban_valid(compact[:end].upper()):
                add(IdentifierKind.IBAN, compact[:end])
                break
    for match in _GERMAN_VAT.finditer(normalised):
        add(IdentifierKind.VAT_ID, match.group())
    for match in _VAT_LABEL.finditer(normalised):
        add(IdentifierKind.VAT_ID, match.group(1))
    for match in _CUSTOMER_LABEL.finditer(normalised):
        add(IdentifierKind.CUSTOMER_NUMBER, match.group(1))
    for match in _PERSONNEL_LABEL.finditer(normalised):
        add(IdentifierKind.PERSONNEL_NUMBER, match.group(1))
    for pattern in (_EMAIL, _WEB):
        for match in pattern.finditer(normalised):
            add(IdentifierKind.EMAIL_DOMAIN, match.group(1))
    return found


def matches(identifier: Identifier, found: tuple[IdentifierKind, str]) -> bool:
    """Whether a found value is the identifier; a domain also stands for its subdomains
    (`service.stadtwerke.de` for `stadtwerke.de`)."""
    kind, value = found
    if kind is not identifier.kind:
        return False
    if kind is IdentifierKind.EMAIL_DOMAIN:
        return value == identifier.value or value.endswith("." + identifier.value)
    return value == identifier.value


# --- learning ---------------------------------------------------------------------------------


def mine_identifiers(
    documents: Iterable[
        tuple[ContactId | None, TagId | None, Iterable[tuple[IdentifierKind, str]]]
    ],
    taken: Iterable[tuple[IdentifierKind, str]] = (),
) -> Mapping[ContactId, list[Identifier]]:
    """The identifiers to learn from confirmed documents (each its contact, its topic and the
    identifiers in its text): a value that stands in at least `LEARN_FROM` documents, all with
    the same contact. A document without a contact counts as another contact. `taken` (entered
    by hand or rejected anywhere) is never learned. A learned value points to a topic if all
    its documents carry that one; otherwise to none."""
    holders: dict[tuple[IdentifierKind, str], list[tuple[ContactId | None, TagId | None]]] = (
        defaultdict(list)
    )
    for contact, topic, found in documents:
        for key in set(found):
            holders[key].append((contact, topic))
    skip = set(taken)
    learned: dict[ContactId, list[Identifier]] = defaultdict(list)
    for (kind, value), pairs in sorted(holders.items()):
        owners = {contact for contact, _ in pairs}
        if (kind, value) in skip or len(pairs) < LEARN_FROM or len(owners) != 1:
            continue
        (owner,) = owners
        topics = {topic for _, topic in pairs}
        topic = next(iter(topics)) if len(topics) == 1 else None
        if owner is not None:
            learned[owner].append(Identifier(kind, value, IdentifierSource.LEARNED, topic))
    return {owner: found[:MAX_IDENTIFIERS] for owner, found in learned.items()}
