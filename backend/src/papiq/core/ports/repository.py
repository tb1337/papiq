"""Metadata persistence: one repository per aggregate, all reached through the unit of work.

First adapter: SQLAlchemy on SQLite or Postgres (M2).

Common rules for every adapter:
- Reads return copies; changing an entity has no effect until `update` and commit.
- `add` keeps the entity's version; `update` succeeds only if the stored version equals the
  entity's version, then increments both. Otherwise it raises ConcurrencyError (at the latest on
  commit).
- Uniqueness (raises ConflictError at the latest on commit): usernames; space names per owner;
  one default space per owner; names of contacts, document types and field definitions, and
  of tags per scope; the original (SHA-256) per document owner. Names compare regardless of
  case.
- `get` raises NotFoundError, `find` returns None.
"""

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from papiq.core.domain.classifier import ClassifierModel
from papiq.core.domain.documents import Document, Sha256
from papiq.core.domain.fields import FieldDefinition
from papiq.core.domain.identifiers import IdentifierKind
from papiq.core.domain.ids import (
    ClassifierModelId,
    ContactId,
    DeliveryId,
    DocumentId,
    DocumentTypeId,
    FieldId,
    RuleApplicationId,
    RuleId,
    SpaceId,
    TagId,
    UserId,
    WebhookId,
)
from papiq.core.domain.master_data import Contact, DocumentType, Tag, TagScope
from papiq.core.domain.pipeline import Lane, NodeRun
from papiq.core.domain.rules import Rule, RuleApplication, RuleVersion
from papiq.core.domain.sorting import sort_key
from papiq.core.domain.spaces import Space
from papiq.core.domain.users import User
from papiq.core.domain.verdict import KnownLabels
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
        """Delete the user; NotFoundError if missing. Documents, spaces, shares and identity
        data that refer to the user must be removed first."""
        ...


class SpaceRepository(Repository[SpaceId, Space], Protocol):
    async def get_default(self, owner: UserId) -> Space:
        """The owner's default space; NotFoundError if there is none."""
        ...

    async def list_accessible(self, user: UserId) -> list[Space]:
        """Spaces the user owns or that are shared with them."""
        ...

    async def remove(self, id: SpaceId) -> None:
        """Delete the space and its shares; NotFoundError if missing. It must hold no
        documents."""
        ...


class ContactRepository(NamedRepository[ContactId, Contact], Protocol): ...


class DocumentTypeRepository(NamedRepository[DocumentTypeId, DocumentType], Protocol): ...


class TagRepository(NamedRepository[TagId, Tag], Protocol):
    async def find_by_name(self, name: str, scope: TagScope = TagScope.DOCUMENT) -> Tag | None:
        """Case-insensitive lookup among the tags of `scope` (names are unique per scope)."""
        ...

    async def remove(self, id: TagId) -> None:
        """Delete it; NotFoundError if missing. No document, contact, identifier, type or tag
        may refer to it."""
        ...


class FieldDefinitionRepository(NamedRepository[FieldId, FieldDefinition], Protocol): ...


type TagSets = tuple[frozenset[TagId], ...]


@dataclass(frozen=True, kw_only=True)
class DocumentFilter:
    """Criteria for listing documents; all given ones must match.

    `tags`: sets of tags; the document carries one tag of each set. The core gives a tag with
    its descendants as one set (`TagTree.with_descendants`), so no adapter walks the tree.
    `contact_groups`, `document_type_groups` likewise: the document's contact (type) is in one
    group of each set. `topics`: the document's topic is one of them (a topic with its
    descendants). `lanes`: one of them, where None stands for documents still in processing.
    `confirmed`: whether a person set or confirmed the metadata (`Document.confirmed_at`).
    `field`: a value for it is set."""

    contact: ContactId | None = None
    document_type: DocumentTypeId | None = None
    tags: TagSets = ()
    contact_groups: TagSets = ()
    document_type_groups: TagSets = ()
    topics: frozenset[TagId] | None = None
    space: SpaceId | None = None
    lanes: frozenset[Lane | None] | None = None
    confirmed: bool | None = None
    field: FieldId | None = None

    def __post_init__(self) -> None:
        for sets in (self.tags, self.contact_groups, self.document_type_groups):
            if any(not tags for tags in sets):
                raise ValueError("a set of tags in a filter is never empty")
        if self.topics is not None and not self.topics:
            raise ValueError("a set of topics in a filter is never empty")


class SortKey(StrEnum):
    CREATED = "created_at"  # the order documents came in, which is their ids'
    DOCUMENT_DATE = "document_date"
    TITLE = "title"  # by `sort_key` of the title


@dataclass(frozen=True)
class DocumentOrder:
    """How a list of documents is sorted: by `key`, descending unless `ascending`, ties by id
    the same way. Documents without a document date come last either way."""

    key: SortKey = SortKey.CREATED
    ascending: bool = False


NEWEST_FIRST = DocumentOrder()


class CountBy(StrEnum):
    """What documents are counted by: their contact, type, topic, each tag, each field with a
    value."""

    CONTACT = "contact"
    DOCUMENT_TYPE = "document_type"
    TOPIC = "topic"
    TAG = "tag"
    FIELD = "field"


@dataclass(frozen=True)
class DocumentPosition:
    """Where a page of a sorted list ended: its last document, and that document's value of
    the sort key (none for `CREATED`, which sorts by id)."""

    id: DocumentId
    value: date | str | None = None

    @classmethod
    def of(cls, document: Document, order: DocumentOrder) -> "DocumentPosition":
        match order.key:
            case SortKey.CREATED:
                return cls(document.id)
            case SortKey.DOCUMENT_DATE:
                return cls(document.id, document.document_date)
            case SortKey.TITLE:
                return cls(document.id, sort_key(document.title))


class DocumentRepository(Repository[DocumentId, Document], Protocol):
    async def find_by_sha256(self, owner: UserId, sha256: Sha256) -> Document | None: ...

    async def list_visible_to(self, user: UserId) -> list[Document]:
        """Documents within the user's reach (`papiq.core.domain.permissions.in_reach`): own
        documents, and green documents in spaces the user owns or that are shared with them.
        """
        ...

    async def field_in_use(
        self,
        field: FieldId,
        *,
        values: Collection[str] | None = None,
        outside_types: Collection[DocumentTypeId] | None = None,
    ) -> bool:
        """Whether any document has a value for `field`; with `values`, a text value that
        is one of them; with `outside_types`, on a document whose type is none of them (a
        document without type counts as outside)."""
        ...

    async def query_visible(
        self,
        user: UserId,
        filter: DocumentFilter,
        *,
        order: DocumentOrder = NEWEST_FIRST,
        after: DocumentPosition | None = None,
        limit: int,
    ) -> list[Document]:
        """Documents the user may read (as `list_visible_to`) that match `filter`, in `order`,
        only those after `after` in it, at most `limit`."""
        ...

    async def query(
        self,
        filter: DocumentFilter,
        *,
        order: DocumentOrder = NEWEST_FIRST,
        after: DocumentPosition | None = None,
        limit: int,
    ) -> list[Document]:
        """As `query_visible`, over every document (for admins)."""
        ...

    async def count_by(self, by: CountBy, user: UserId | None) -> dict[UUID, int]:
        """How many documents the user may read (as `list_visible_to`; None: every document)
        have each contact, type, topic or tag, or a value for each field. Only ids with
        documents."""
        ...

    async def count_tagged(
        self, sets: Mapping[UUID, Collection[TagId]], user: UserId | None
    ) -> dict[UUID, int]:
        """For each key, how many documents the user may read (as `count_by`) carry a tag of
        its set, each document once (the core gives a tag with its descendants). Only keys with
        documents."""
        ...

    async def remove(self, id: DocumentId) -> None:
        """Delete the document and its processing log; NotFoundError if it does not exist."""
        ...

    async def count_confirmed(self, *, after: datetime | None = None) -> int:
        """Documents a person set or confirmed the metadata of, after `after` if given (the
        confirmations since the classifier was trained)."""
        ...

    async def confirmed_labels(self, reader: UserId) -> KnownLabels:
        """The contacts, document types, topics and tags the confirmed documents `reader` may
        read carry: what a document similar to one of theirs can name."""
        ...

    async def exists(
        self,
        *,
        owner: UserId | None = None,
        space: SpaceId | None = None,
        contact: ContactId | None = None,
        document_type: DocumentTypeId | None = None,
        tag: TagId | None = None,
        topic: TagId | None = None,
        field: FieldId | None = None,
        sha256: Sha256 | None = None,
    ) -> bool:
        """Whether any document matches all given criteria, regardless of who may read it
        (for checks before deleting what documents refer to). At least one is required."""
        ...


class DocumentIdentifierRepository(Protocol):
    """The identifiers each document shows, in its text or its codes, as the facts signal last
    found them: for telling on how many contacts' documents a value stands (the IBAN of the
    owner's own account stands on many)."""

    async def replace(
        self, document: DocumentId, identifiers: Collection[tuple[IdentifierKind, str]]
    ) -> None:
        """Make `identifiers` (kind and normalised value) those of `document`."""
        ...

    async def contacts_of(
        self, identifiers: Collection[tuple[IdentifierKind, str]]
    ) -> dict[tuple[IdentifierKind, str], frozenset[ContactId]]:
        """For each of `identifiers`, the contacts of the confirmed documents that show it;
        one that no confirmed document with a contact shows is left out."""
        ...


class ProcessingLog(Protocol):
    """Append-only log of node executions."""

    async def append(self, run: NodeRun) -> None: ...

    async def list_for(self, document: DocumentId) -> list[NodeRun]:
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


class ClassifierModelRepository(Protocol):
    """The trained classifiers (their files are in the object store)."""

    async def add(self, model: ClassifierModel) -> None: ...

    async def latest(self) -> ClassifierModel | None:
        """The model with the highest number."""
        ...

    async def list_all(self) -> list[ClassifierModel]:
        """Newest first."""
        ...

    async def remove(self, id: ClassifierModelId) -> None: ...


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
