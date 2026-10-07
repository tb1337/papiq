"""Metadata persistence: one repository per aggregate, all reached through the unit of work.

First adapter: SQLAlchemy on SQLite or Postgres (M2).

Common rules for every adapter:
- Reads return copies; changing an entity has no effect until `update` and commit.
- `add` keeps the entity's version; `update` succeeds only if the stored version equals the
  entity's version, then increments both. Otherwise it raises ConcurrencyError (at the latest on
  commit).
- Uniqueness (raises ConflictError at the latest on commit): usernames; drawer names per owner;
  one default drawer per owner; names of contacts, document types, tags and attribute
  definitions; the original (SHA-256) per document owner. Names compare regardless of case.
- `get` raises NotFoundError, `find` returns None.
"""

from dataclasses import dataclass
from typing import Protocol

from papiq.core.domain.attributes import AttributeDefinition
from papiq.core.domain.documents import Document, Sha256
from papiq.core.domain.drawers import Drawer
from papiq.core.domain.ids import (
    AttributeId,
    ContactId,
    DocumentId,
    DocumentTypeId,
    DrawerId,
    TagId,
    UserId,
)
from papiq.core.domain.master_data import Contact, DocumentType, Tag
from papiq.core.domain.pipeline import Lane, StepRun
from papiq.core.domain.users import User


class Repository[K, E](Protocol):
    async def get(self, id: K) -> E: ...

    async def find(self, id: K) -> E | None: ...

    async def add(self, entity: E) -> None: ...

    async def update(self, entity: E) -> None: ...

    async def list_all(self) -> list[E]:
        """All entities, in no particular order."""
        ...


class NamedRepository[K, E](Repository[K, E], Protocol):
    async def find_by_name(self, name: str) -> E | None:
        """Case-insensitive lookup."""
        ...

    async def remove(self, id: K) -> None:
        """Delete it; NotFoundError if missing. No document may refer to it."""
        ...


class UserRepository(Repository[UserId, User], Protocol):
    async def find_by_username(self, username: str) -> User | None:
        """Case-insensitive lookup."""
        ...

    async def remove(self, id: UserId) -> None:
        """Delete the user; NotFoundError if missing. Documents, drawers, shares and identity
        data that refer to the user must be removed first."""
        ...


class DrawerRepository(Repository[DrawerId, Drawer], Protocol):
    async def get_default(self, owner: UserId) -> Drawer:
        """The owner's default drawer; NotFoundError if there is none."""
        ...

    async def list_accessible(self, user: UserId) -> list[Drawer]:
        """Drawers the user owns or that are shared with them."""
        ...

    async def remove(self, id: DrawerId) -> None:
        """Delete the drawer and its shares; NotFoundError if missing. It must hold no
        documents."""
        ...


class ContactRepository(NamedRepository[ContactId, Contact], Protocol): ...


class DocumentTypeRepository(NamedRepository[DocumentTypeId, DocumentType], Protocol): ...


class TagRepository(NamedRepository[TagId, Tag], Protocol): ...


class AttributeDefinitionRepository(
    NamedRepository[AttributeId, AttributeDefinition], Protocol
): ...


@dataclass(frozen=True, kw_only=True)
class DocumentFilter:
    """Criteria for listing documents; all given ones must match. `tags`: every one of them.
    `lanes`: one of them, where None stands for documents still in processing."""

    contact: ContactId | None = None
    document_type: DocumentTypeId | None = None
    tags: frozenset[TagId] = frozenset()
    drawer: DrawerId | None = None
    lanes: frozenset[Lane | None] | None = None


class DocumentRepository(Repository[DocumentId, Document], Protocol):
    async def find_by_sha256(self, owner: UserId, sha256: Sha256) -> Document | None: ...

    async def list_visible_to(self, user: UserId) -> list[Document]:
        """Documents the user may read, by the rules of `papiq.core.domain.permissions`:
        own documents, and green documents in drawers the user owns or that are shared with them.
        """
        ...

    async def query_visible(
        self,
        user: UserId,
        filter: DocumentFilter,
        *,
        before: DocumentId | None = None,
        limit: int,
    ) -> list[Document]:
        """Documents the user may read (as `list_visible_to`) that match `filter`, newest first
        (by id, descending), only those with an id below `before`, at most `limit`."""
        ...

    async def remove(self, id: DocumentId) -> None:
        """Delete the document and its processing log; NotFoundError if it does not exist."""
        ...

    async def exists(
        self,
        *,
        owner: UserId | None = None,
        drawer: DrawerId | None = None,
        contact: ContactId | None = None,
        document_type: DocumentTypeId | None = None,
        tag: TagId | None = None,
        attribute: AttributeId | None = None,
    ) -> bool:
        """Whether any document matches all given criteria, regardless of who may read it
        (for checks before deleting what documents refer to). At least one is required."""
        ...


class ProcessingLog(Protocol):
    """Append-only log of step executions."""

    async def append(self, run: StepRun) -> None: ...

    async def list_for(self, document: DocumentId) -> list[StepRun]:
        """All entries of the document in the order they were appended."""
        ...
