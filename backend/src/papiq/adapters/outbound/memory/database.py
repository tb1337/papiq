"""Committed state shared by the in-memory unit of work, job queue and event bus."""

from collections.abc import Callable, Hashable, Iterable
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from papiq.core.domain.attributes import AttributeDefinition
from papiq.core.domain.documents import Document
from papiq.core.domain.drawers import Drawer
from papiq.core.domain.events import DomainEvent
from papiq.core.domain.ids import JobId
from papiq.core.domain.jobs import Job
from papiq.core.domain.master_data import MasterData
from papiq.core.domain.pipeline import StepRun
from papiq.core.domain.users import User
from papiq.core.domain.validation import name_key


@dataclass(frozen=True)
class Table:
    """A kind of entity: its name in errors and the keys that must be unique across the table."""

    name: str
    unique_keys: Callable[[Any], Iterable[Hashable]]


def _user_keys(user: User) -> Iterable[Hashable]:
    return [("username", name_key(user.username))]


def _drawer_keys(drawer: Drawer) -> Iterable[Hashable]:
    keys: list[Hashable] = [("name", drawer.owner_id, name_key(drawer.name))]
    if drawer.is_default:
        keys.append(("default", drawer.owner_id))
    return keys


def _name_keys(item: MasterData) -> Iterable[Hashable]:
    return [("name", name_key(item.name))]


def _attribute_keys(item: AttributeDefinition) -> Iterable[Hashable]:
    return _name_keys(item)


def _document_keys(document: Document) -> Iterable[Hashable]:
    return [("sha256", document.owner_id, document.sha256)]


USERS = Table("user", _user_keys)
DRAWERS = Table("drawer", _drawer_keys)
CONTACTS = Table("contact", _name_keys)
DOCUMENT_TYPES = Table("document type", _name_keys)
TAGS = Table("tag", _name_keys)
ATTRIBUTES = Table("attribute", _attribute_keys)
DOCUMENTS = Table("document", _document_keys)


@dataclass
class MemoryDatabase:
    """What has been committed. Rows are private copies; insertion order is creation order."""

    tables: dict[str, dict[UUID, Any]] = field(default_factory=dict)
    processing_log: list[StepRun] = field(default_factory=list)
    outbox: list[DomainEvent] = field(default_factory=list)
    jobs: dict[JobId, Job] = field(default_factory=dict)

    def rows(self, table: Table) -> dict[UUID, Any]:
        return self.tables.setdefault(table.name, {})
