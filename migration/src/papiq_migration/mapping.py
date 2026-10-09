"""How Paperless-ngx objects map to Papiq: pure functions, no I/O."""

import hashlib
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit

NOTES_LIMIT = 20_000  # characters of the notes field (the upload's metadata is limited)
TITLE_LIMIT = 500
NAME_LIMIT = 200

ASN = "ASN"
NOTES = "Notizen"
READ, READ_WRITE = "read", "read_write"
_MONEY = re.compile(r"(?P<currency>[A-Za-z]{3})?\s*(?P<amount>[-+]?\d+(?:\.\d+)?)")


def norm(name: str) -> str:
    """Names are compared without case and surrounding space, as Papiq does."""
    return name.strip().casefold()


@dataclass(frozen=True)
class FieldSpec:
    """The Papiq field definition for a Paperless custom field (or ASN, notes)."""

    key: str  # "field:<id>", "asn", "notes"
    name: str
    data_type: str  # Papiq: text, number, amount, date, boolean, choice, link
    choices: tuple[str, ...] = ()


_TYPES = {
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
ASN_SPEC = FieldSpec("asn", ASN, "number")
NOTES_SPEC = FieldSpec("notes", NOTES, "text")


def field_key(id: int) -> str:
    return f"field:{id}"


def select_options(custom_field: dict[str, Any]) -> dict[str, str]:
    """The labels of a select field by option id."""
    options = (custom_field.get("extra_data") or {}).get("select_options") or []
    return {str(o["id"]): str(o["label"]).strip() for o in options if isinstance(o, dict)}


def field_spec(custom_field: dict[str, Any]) -> tuple[FieldSpec | None, list[str]]:
    """The field for a custom field, or None with the reasons why there is none."""
    kind = custom_field["data_type"]
    name = str(custom_field["name"]).strip()[:NAME_LIMIT]
    if kind not in _TYPES:
        return None, [f"data type '{kind}' has no counterpart in Papiq: values are not taken over"]
    notes: list[str] = []
    choices: tuple[str, ...] = ()
    if kind == "select":
        labels = list(dict.fromkeys(select_options(custom_field).values()))
        if len(labels) != len(select_options(custom_field)):
            notes.append("options with the same label were merged")
        choices = tuple(labels)
    return FieldSpec(field_key(custom_field["id"]), name, _TYPES[kind], choices), notes


@dataclass(frozen=True)
class Converted:
    """A custom field value as Papiq's JSON, or None with the reason it is not taken over."""

    value: Any = None
    note: str | None = None


def convert_value(custom_field: dict[str, Any], raw: Any, currency: str) -> Converted:
    if raw is None or raw == "" or raw == []:
        return Converted(note="empty value")
    kind = custom_field["data_type"]
    if kind not in _TYPES:
        return Converted(note=f"{kind}: not taken over ({_short(raw)})")
    try:
        match kind:
            case "string" | "longtext":
                text = str(raw).strip()
                return Converted(text) if text else Converted(note="empty value")
            case "url":
                parts = urlsplit(str(raw).strip())
                if parts.scheme not in ("http", "https") or not parts.hostname:
                    return Converted(note=f"not an absolute http(s) URL: {_short(raw)}")
                if any(char.isspace() for char in str(raw).strip()):
                    return Converted(note=f"not an absolute http(s) URL: {_short(raw)}")
                return Converted(str(raw).strip())
            case "date":
                return Converted(date.fromisoformat(str(raw)[:10]).isoformat())
            case "boolean":
                return (
                    Converted(raw)
                    if isinstance(raw, bool)
                    else Converted(note=f"not a boolean: {_short(raw)}")
                )
            case "integer" | "float":
                if isinstance(raw, bool):
                    return Converted(note=f"not a number: {_short(raw)}")
                number = Decimal(str(raw))
                if not number.is_finite():
                    return Converted(note=f"not a number: {_short(raw)}")
                return Converted(format(number, "f"))
            case "monetary":
                return _money(custom_field, raw, currency)
            case "select":
                label = select_options(custom_field).get(str(raw))
                if label is None:
                    return Converted(note=f"option {_short(raw)} does not exist")
                return Converted(label)
    except (ValueError, InvalidOperation):
        return Converted(note=f"invalid {kind} value: {_short(raw)}")
    return Converted(note=f"{kind}: not taken over")


def _money(custom_field: dict[str, Any], raw: Any, currency: str) -> Converted:
    """Paperless stores `EUR12.50`, or `12.50` for the field's default currency."""
    found = _MONEY.fullmatch(str(raw).strip())
    if found is None:
        return Converted(note=f"not an amount: {_short(raw)}")
    default = (custom_field.get("extra_data") or {}).get("default_currency")
    code = (found["currency"] or default or currency).upper()
    return Converted({"amount": format(Decimal(found["amount"]), "f"), "currency": code})


def _short(value: Any) -> str:
    text = repr(value)
    return text if len(text) <= 60 else text[:57] + "..."


# --- document fields --------------------------------------------------------------------------


def document_title(document: dict[str, Any]) -> tuple[str, list[str]]:
    title = str(document.get("title") or "").strip()
    notes: list[str] = []
    if not title:
        name = str(document.get("original_file_name") or "").rsplit(".", 1)[0].strip()
        title = name or f"Document {document['id']}"
        notes.append("the title was empty: taken from the file name")
    if len(title) > TITLE_LIMIT:
        title = title[:TITLE_LIMIT].rstrip()
        notes.append(f"the title was shortened to {TITLE_LIMIT} characters")
    return title, notes


def document_date(document: dict[str, Any]) -> str | None:
    created = document.get("created")
    return str(created)[:10] if created else None


def notes_text(notes: list[dict[str, Any]]) -> tuple[str | None, list[str]]:
    """The notes of a document as one text: `date, author: note`, one paragraph each."""
    paragraphs = []
    for note in sorted(notes, key=lambda item: item.get("created") or ""):
        user = note.get("user") or {}
        author = user.get("username") if isinstance(user, dict) else None
        stamp = str(note.get("created") or "")[:10]
        head = ", ".join(part for part in (stamp, author) if part)
        text = str(note.get("note") or "").strip()
        if text:
            paragraphs.append(f"{head}: {text}" if head else text)
    if not paragraphs:
        return None, []
    joined = "\n\n".join(paragraphs)
    if len(joined) > NOTES_LIMIT:
        return joined[:NOTES_LIMIT].rstrip(), [
            f"the notes were shortened to {NOTES_LIMIT} characters"
        ]
    return joined, []


# --- permissions ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Access:
    """Who owns a document and who else may read or write it (by Papiq user name)."""

    owner: str | None
    shares: tuple[tuple[str, str], ...]  # (username, level), sorted by name
    notes: tuple[str, ...] = ()


def access_of(
    document: dict[str, Any],
    users: dict[int, dict[str, Any]],
) -> Access:
    """Readers and writers of a document with groups resolved to their members. The owner is
    left out of the shares; users that are deactivated or gone cannot be shared with."""
    notes: list[str] = []
    owner_id = document.get("owner")
    owner = None
    if owner_id is None:
        notes.append("no owner in Paperless: the executing admin owns it")
    elif owner_id not in users:
        notes.append(f"owner {owner_id} no longer exists in Paperless: the executing admin owns it")
    else:
        owner = str(users[owner_id]["username"])
    permissions = document.get("permissions") or {}
    members: dict[int, set[int]] = {}
    for user in users.values():
        for group in user.get("groups") or []:
            members.setdefault(int(group), set()).add(int(user["id"]))

    def resolve(kind: str) -> set[int]:
        part = permissions.get(kind) or {}
        ids = {int(item) for item in part.get("users") or []}
        for group in part.get("groups") or []:
            ids |= members.get(int(group), set())
        return ids

    writers, viewers = resolve("change"), resolve("view")
    shares: dict[str, str] = {}
    for user_id in sorted(writers | viewers):
        if user_id == owner_id:
            continue
        member = users.get(user_id)
        if member is None:
            notes.append(f"shared with user {user_id}, who no longer exists: not shared")
            continue
        if not member.get("is_active", True):
            notes.append(f"shared with inactive user {member['username']}: not shared")
            continue
        shares[str(member["username"])] = READ_WRITE if user_id in writers else READ
    return Access(
        owner, tuple(sorted(shares.items(), key=lambda item: norm(item[0]))), tuple(notes)
    )


def drawer_name(shares: tuple[tuple[str, str], ...]) -> str:
    """The name of the drawer for a combination of readers and writers; it names the users, so
    the same combination gets the same drawer."""
    words = {READ: "lesen", READ_WRITE: "schreiben"}
    text = "Geteilt: " + ", ".join(f"{user} ({words[level]})" for user, level in shares)
    if len(text) <= NAME_LIMIT:
        return text
    key = hashlib.sha256(repr(shares).encode()).hexdigest()[:8]
    return text[: NAME_LIMIT - 13].rstrip() + f"... #{key}"
