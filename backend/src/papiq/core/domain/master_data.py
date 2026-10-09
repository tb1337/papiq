"""Master data: contacts, document types and tags. Global, no owner; only admins change them."""

from dataclasses import dataclass
from datetime import datetime
from typing import Self

from papiq.core.domain.ids import ContactId, DocumentTypeId, TagId, new_id
from papiq.core.domain.validation import require_name, require_utc


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
    """The other party of a document (Paperless-ngx: correspondent)."""

    id: ContactId

    @classmethod
    def create(cls, *, name: str, now: datetime) -> Self:
        return cls(id=ContactId(new_id()), name=name, created_at=now)


@dataclass(kw_only=True)
class DocumentType(MasterData):
    """The kind of a document; field definitions can be bound to it."""

    id: DocumentTypeId

    @classmethod
    def create(cls, *, name: str, now: datetime) -> Self:
        return cls(id=DocumentTypeId(new_id()), name=name, created_at=now)


@dataclass(kw_only=True)
class Tag(MasterData):
    """A label; tags carry no permissions."""

    id: TagId

    @classmethod
    def create(cls, *, name: str, now: datetime) -> Self:
        return cls(id=TagId(new_id()), name=name, created_at=now)
