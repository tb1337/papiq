"""Master data: contacts, document types and tags. Global, no owner; only admins change them."""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Self

from papiq.core.domain.errors import ValidationError
from papiq.core.domain.ids import ContactId, DocumentTypeId, TagId, new_id
from papiq.core.domain.validation import name_key, require_name, require_utc

MAX_DESCRIPTION = 300
"""Characters of a document type's description; it goes into every classification prompt."""


@dataclass(kw_only=True)
class MasterData:
    """Common part of all master data: a name unique per kind regardless of case."""

    name: str
    created_at: datetime
    version: int = 1

    def __post_init__(self) -> None:
        self.name = require_name(self.name)
        self.created_at = require_utc(self.created_at, "created_at")

    def rename(self, name: str) -> None:
        self.name = require_name(name)


@dataclass(kw_only=True)
class Contact(MasterData):
    """The other party of a document (Paperless-ngx: correspondent).

    `aliases` are other names the contact is written as in documents ("Nord Krankenversicherung
    AG" for "Nord Versicherungsgruppe"); classification matches them like the name. They are
    unique regardless of case and differ from the name; across contacts, names and aliases are
    unique too (see `MasterDataService`)."""

    id: ContactId
    aliases: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        super().__post_init__()
        self.aliases = _aliases(self.name, self.aliases)

    @classmethod
    def create(cls, *, name: str, now: datetime, aliases: Iterable[str] = ()) -> Self:
        return cls(id=ContactId(new_id()), name=name, created_at=now, aliases=list(aliases))

    def rename(self, name: str) -> None:
        """An alias that becomes the name is no longer an alias."""
        name = require_name(name)
        self.aliases = [alias for alias in self.aliases if name_key(alias) != name_key(name)]
        self.name = name

    def set_aliases(self, aliases: Iterable[str]) -> None:
        self.aliases = _aliases(self.name, aliases)

    def names(self) -> list[str]:
        """The name and the aliases."""
        return [self.name, *self.aliases]

    def is_named(self, name: str) -> bool:
        """Whether `name` is the name or an alias, regardless of case."""
        key = name_key(name)
        return any(name_key(own) == key for own in self.names())


@dataclass(kw_only=True)
class DocumentType(MasterData):
    """The kind of a document; field definitions can be bound to it. `description` tells the
    language model what belongs to the type ("pay slips, Entgeltbescheinigung, ...")."""

    id: DocumentTypeId
    description: str | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        self.description = _description(self.description)

    @classmethod
    def create(cls, *, name: str, now: datetime, description: str | None = None) -> Self:
        return cls(id=DocumentTypeId(new_id()), name=name, created_at=now, description=description)

    def describe(self, description: str | None) -> None:
        self.description = _description(description)


@dataclass(kw_only=True)
class Tag(MasterData):
    """A label; tags carry no permissions."""

    id: TagId

    @classmethod
    def create(cls, *, name: str, now: datetime) -> Self:
        return cls(id=TagId(new_id()), name=name, created_at=now)


def _aliases(name: str, aliases: Iterable[str]) -> list[str]:
    """Stripped, without duplicates (regardless of case); none may be the name."""
    result: list[str] = []
    keys = {name_key(name)}
    for alias in aliases:
        alias = require_name(alias, "alias")
        key = name_key(alias)
        if key == name_key(name):
            raise ValidationError(f"the alias '{alias}' is the contact's name")
        if key not in keys:
            keys.add(key)
            result.append(alias)
    return result


def _description(description: str | None) -> str | None:
    text = (description or "").strip()
    if len(text) > MAX_DESCRIPTION:
        raise ValidationError(f"description must not be longer than {MAX_DESCRIPTION} characters")
    return text or None
