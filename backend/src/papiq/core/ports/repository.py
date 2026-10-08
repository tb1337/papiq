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

from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from papiq.core.domain.attributes import AttributeDefinition
from papiq.core.domain.documents import Document, Sha256
from papiq.core.domain.drawers import Drawer
from papiq.core.domain.ids import (
    AttributeId,
    ContactId,
    DeliveryId,
    DocumentId,
    DocumentTypeId,
    DrawerId,
    RuleApplicationId,
    RuleId,
    TagId,
    UserId,
    WebhookId,
)
from papiq.core.domain.master_data import Contact, DocumentType, Tag
from papiq.core.domain.pipeline import Lane, StepRun
from papiq.core.domain.rules import Rule, RuleApplication, RuleVersion
from papiq.core.domain.users import User
from papiq.core.domain.webhooks import Webhook, WebhookDelivery


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
        """Documents within the user's reach (`papiq.core.domain.permissions.in_reach`): own
        documents, and green documents in drawers the user owns or that are shared with them.
        """
        ...

    async def attribute_in_use(
        self,
        attribute: AttributeId,
        *,
        values: Collection[str] | None = None,
        outside_types: Collection[DocumentTypeId] | None = None,
    ) -> bool:
        """Whether any document has a value for `attribute`; with `values`, a text value that
        is one of them; with `outside_types`, on a document whose type is none of them (a
        document without type counts as outside)."""
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

    async def query(
        self, filter: DocumentFilter, *, before: DocumentId | None = None, limit: int
    ) -> list[Document]:
        """As `query_visible`, over every document (for admins)."""
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
        sha256: Sha256 | None = None,
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


class RuleRepository(Protocol):
    """Rules with their versions. A rule is stored with its current version; `update` stores a
    new current version as well and keeps the earlier ones. Deleted rules (`deleted_at`) stay
    stored for their versions; `get` and `find` return them, the lists leave them out."""

    async def get(self, id: RuleId) -> Rule: ...

    async def find(self, id: RuleId) -> Rule | None: ...

    async def add(self, rule: Rule) -> None: ...

    async def update(self, rule: Rule) -> None:
        """Version check as for every repository. If `rule.current` is a version that is not
        stored yet, it is added."""
        ...

    async def list_for(
        self, *, owners: Collection[UserId] | None, include_global: bool
    ) -> list[Rule]:
        """Rules that are not deleted: user rules of `owners` (None: of every user), and global
        rules if `include_global`."""
        ...

    async def versions(self, id: RuleId) -> list[RuleVersion]:
        """All versions of the rule, oldest first; NotFoundError if the rule does not exist."""
        ...

    async def get_version(self, id: RuleId, number: int) -> RuleVersion:
        """NotFoundError if the rule or the version does not exist."""
        ...

    async def remove_for_owner(self, owner: UserId) -> int:
        """Delete the user's rules with their versions, and all applications of these rules or
        by this user, for good; returns how many rules."""
        ...


class RuleApplicationRepository(Protocol):
    """Applications of a rule to existing documents (`RuleApplication`)."""

    async def get(self, id: RuleApplicationId) -> RuleApplication: ...

    async def find(self, id: RuleApplicationId) -> RuleApplication | None: ...

    async def add(self, application: RuleApplication) -> None: ...

    async def update(self, application: RuleApplication) -> None: ...


class WebhookRepository(Protocol):
    """Webhooks with the log of their deliveries."""

    async def get(self, id: WebhookId) -> Webhook: ...

    async def find(self, id: WebhookId) -> Webhook | None: ...

    async def add(self, webhook: Webhook) -> None: ...

    async def update(self, webhook: Webhook) -> None: ...

    async def remove(self, id: WebhookId) -> None:
        """Delete the webhook with its deliveries; no-op if it does not exist."""
        ...

    async def remove_for_owner(self, owner: UserId) -> int:
        """Delete the user's webhooks with their deliveries; returns how many webhooks."""
        ...

    async def list_for_owner(self, owner: UserId) -> list[Webhook]:
        """The user's webhooks, oldest first."""
        ...

    async def list_all(self) -> list[Webhook]:
        """Everybody's webhooks, oldest first."""
        ...

    async def count_for_owner(self, owner: UserId) -> int: ...

    async def list_active_for(self, event_type: str) -> list[Webhook]:
        """The active webhooks that want the event type (it is listed, or they want all)."""
        ...

    async def add_delivery(self, delivery: WebhookDelivery) -> None:
        """Log an attempt. The webhook must exist (NotFoundError otherwise): one that was
        deleted meanwhile has nothing to log for."""
        ...

    async def deliveries(
        self, webhook: WebhookId, *, before: DeliveryId | None = None, limit: int = 50
    ) -> list[WebhookDelivery]:
        """The log of a webhook, newest first (by id), the page after `before`."""
        ...

    async def purge_deliveries(self, *, before: datetime) -> int:
        """Remove deliveries that started before `before`; returns how many."""
        ...
