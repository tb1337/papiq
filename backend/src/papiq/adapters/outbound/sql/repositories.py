"""Repositories and processing log on SQL tables, with the explicit mapping between domain
objects and rows.

`update` is optimistic: `UPDATE ... WHERE id = :id AND version = :version`. If no row matches,
the entity is missing (NotFoundError) or was changed by someone else (ConcurrencyError). On
Postgres, a concurrent update of the same row waits for the other transaction and then finds
the new version; on SQLite, writers are serialized by the write lock.
"""

from collections import defaultdict
from collections.abc import Collection, Iterable, Sequence
from datetime import timedelta
from decimal import Decimal
from typing import Any, ClassVar
from uuid import UUID

from sqlalchemy import ColumnElement, Row, Table, and_, delete, insert, or_, select, true, update

from papiq.adapters.outbound.sql import tables as t
from papiq.adapters.outbound.sql.transaction import Transaction
from papiq.core.domain.attributes import (
    AttributeDefinition,
    AttributeType,
    AttributeValue,
    Money,
    Url,
)
from papiq.core.domain.documents import Channel, Document, Sha256
from papiq.core.domain.drawers import Drawer, ShareLevel
from papiq.core.domain.errors import ConcurrencyError, NotFoundError
from papiq.core.domain.ids import (
    AttributeId,
    ContactId,
    DocumentId,
    DocumentTypeId,
    DrawerId,
    TagId,
    UserId,
)
from papiq.core.domain.master_data import Contact, DocumentType, MasterData, Tag
from papiq.core.domain.pipeline import (
    Lane,
    Outcome,
    Processing,
    ProcessingStatus,
    Step,
    StepResult,
    StepRun,
)
from papiq.core.domain.users import Role, User
from papiq.core.domain.validation import name_key
from papiq.core.ports.repository import DocumentFilter

type Values = dict[str, Any]

_MICROSECOND = timedelta(microseconds=1)


class SqlRepository[K: UUID, E]:
    """Common part: a main table with `id` and `version`, optionally child tables."""

    kind: ClassVar[str]
    table: ClassVar[Table]

    def __init__(self, transaction: Transaction) -> None:
        self._tx = transaction

    async def get(self, id: K) -> E:
        entity = await self.find(id)
        if entity is None:
            raise NotFoundError(self.kind, id)
        return entity

    async def find(self, id: K) -> E | None:
        found = await self._load(self.table.c.id == id)
        return found[0] if found else None

    async def add(self, entity: E) -> None:
        await self._tx.write(insert(self.table).values(self._values(entity)))
        await self._write_children(entity)

    async def update(self, entity: E) -> None:
        id, version = self._id(entity), self._version(entity)
        result = await self._tx.write(
            update(self.table)
            .where(self.table.c.id == id, self.table.c.version == version)
            .values({**self._values(entity), "version": version + 1})
        )
        if result.rowcount == 0:
            exists = await self._tx.read(select(self.table.c.id).where(self.table.c.id == id))
            if exists.first() is None:
                raise NotFoundError(self.kind, id)
            raise ConcurrencyError(f"{self.kind} {id} was changed concurrently")
        entity.version = version + 1  # type: ignore[attr-defined]
        await self._delete_children([id])
        await self._write_children(entity)

    async def list_all(self) -> list[E]:
        return await self._load(true())

    async def remove(self, id: K) -> None:
        """Child tables go with the row (ON DELETE CASCADE)."""
        result = await self._tx.write(delete(self.table).where(self.table.c.id == id))
        if result.rowcount == 0:
            raise NotFoundError(self.kind, id)

    async def _load(self, where: ColumnElement[bool]) -> list[E]:
        rows = (await self._tx.read(select(self.table).where(where))).all()
        return await self._entities(rows)

    async def _entities(self, rows: Sequence[Row[Any]]) -> list[E]:
        return [self._entity(row) for row in rows]

    # --- mapping, implemented per entity -------------------------------------------------------

    def _values(self, entity: E) -> Values:
        """Column values of the main table, including id and version."""
        raise NotImplementedError

    def _entity(self, row: Row[Any]) -> E:
        raise NotImplementedError

    async def _write_children(self, entity: E) -> None:
        """Insert the rows of child tables."""

    async def _delete_children(self, ids: list[K]) -> None:
        """Delete the rows of child tables."""

    @staticmethod
    def _id(entity: E) -> K:
        return entity.id  # type: ignore[attr-defined, no-any-return]

    @staticmethod
    def _version(entity: E) -> int:
        return entity.version  # type: ignore[attr-defined, no-any-return]

    async def _insert_many(self, table: Table, rows: list[Values]) -> None:
        if rows:
            await self._tx.write(insert(table), rows)

    async def _children(
        self, table: Table, parent: str, ids: Iterable[UUID]
    ) -> dict[UUID, list[Row[Any]]]:
        """Rows of a child table, grouped by the parent column."""
        grouped: dict[UUID, list[Row[Any]]] = defaultdict(list)
        id_list = list(ids)
        if id_list:
            column = table.c[parent]
            for row in (await self._tx.read(select(table).where(column.in_(id_list)))).all():
                grouped[row._mapping[parent]].append(row)
        return grouped


# --- users --------------------------------------------------------------------------------------


class SqlUserRepository(SqlRepository[UserId, User]):
    kind = "user"
    table = t.users

    async def find_by_username(self, username: str) -> User | None:
        found = await self._load(t.users.c.username_key == name_key(username))
        return found[0] if found else None

    def _values(self, entity: User) -> Values:
        return {
            "id": entity.id,
            "username": entity.username,
            "username_key": name_key(entity.username),
            "role": entity.role.value,
            "created_at": entity.created_at,
            "active": entity.active,
            "version": entity.version,
        }

    def _entity(self, row: Row[Any]) -> User:
        return User(
            id=UserId(row.id),
            username=row.username,
            role=Role(row.role),
            created_at=row.created_at,
            active=row.active,
            version=row.version,
        )


# --- drawers ------------------------------------------------------------------------------------


class SqlDrawerRepository(SqlRepository[DrawerId, Drawer]):
    kind = "drawer"
    table = t.drawers

    async def get_default(self, owner: UserId) -> Drawer:
        found = await self._load(and_(t.drawers.c.owner_id == owner, t.drawers.c.is_default))
        if not found:
            raise NotFoundError("default drawer of user", owner)
        return found[0]

    async def list_accessible(self, user: UserId) -> list[Drawer]:
        return await self._load(t.drawers.c.id.in_(accessible_drawer_ids(user)))

    async def _entities(self, rows: Sequence[Row[Any]]) -> list[Drawer]:
        shares = await self._children(t.drawer_shares, "drawer_id", (row.id for row in rows))
        return [
            Drawer(
                id=DrawerId(row.id),
                owner_id=UserId(row.owner_id),
                name=row.name,
                is_default=row.is_default,
                shares={UserId(share.user_id): ShareLevel(share.level) for share in shares[row.id]},
                created_at=row.created_at,
                version=row.version,
            )
            for row in rows
        ]

    def _values(self, entity: Drawer) -> Values:
        return {
            "id": entity.id,
            "owner_id": entity.owner_id,
            "name": entity.name,
            "name_key": name_key(entity.name),
            "is_default": entity.is_default,
            "created_at": entity.created_at,
            "version": entity.version,
        }

    async def _write_children(self, entity: Drawer) -> None:
        await self._insert_many(
            t.drawer_shares,
            [
                {"drawer_id": entity.id, "user_id": user_id, "level": level.value}
                for user_id, level in entity.shares.items()
            ],
        )

    async def _delete_children(self, ids: list[DrawerId]) -> None:
        await self._tx.write(delete(t.drawer_shares).where(t.drawer_shares.c.drawer_id.in_(ids)))


def _visible_to(user: UserId) -> ColumnElement[bool]:
    """The reach of `papiq.core.domain.permissions`: own documents, and green documents in
    drawers the user owns or that are shared with them."""
    documents = t.documents
    return or_(
        documents.c.owner_id == user,
        and_(
            documents.c.lane == Lane.GREEN.value,
            documents.c.drawer_id.in_(accessible_drawer_ids(user)),
        ),
    )


def accessible_drawer_ids(user: UserId) -> Any:
    """Subquery: ids of the drawers the user owns or that are shared with them."""
    return (
        select(t.drawers.c.id)
        .where(t.drawers.c.owner_id == user)
        .union(select(t.drawer_shares.c.drawer_id).where(t.drawer_shares.c.user_id == user))
    )


# --- master data --------------------------------------------------------------------------------


class SqlNamedRepository[K: UUID, E: MasterData](SqlRepository[K, E]):
    async def find_by_name(self, name: str) -> E | None:
        found = await self._load(self.table.c.name_key == name_key(name))
        return found[0] if found else None

    def _values(self, entity: E) -> Values:
        return {
            "id": self._id(entity),
            "name": entity.name,
            "name_key": name_key(entity.name),
            "created_at": entity.created_at,
            "version": entity.version,
        }


class SqlContactRepository(SqlNamedRepository[ContactId, Contact]):
    kind = "contact"
    table = t.contacts

    def _entity(self, row: Row[Any]) -> Contact:
        return Contact(
            id=ContactId(row.id), name=row.name, created_at=row.created_at, version=row.version
        )


class SqlDocumentTypeRepository(SqlNamedRepository[DocumentTypeId, DocumentType]):
    kind = "document type"
    table = t.document_types

    def _entity(self, row: Row[Any]) -> DocumentType:
        return DocumentType(
            id=DocumentTypeId(row.id),
            name=row.name,
            created_at=row.created_at,
            version=row.version,
        )


class SqlTagRepository(SqlNamedRepository[TagId, Tag]):
    kind = "tag"
    table = t.tags

    def _entity(self, row: Row[Any]) -> Tag:
        return Tag(id=TagId(row.id), name=row.name, created_at=row.created_at, version=row.version)


class SqlAttributeRepository(SqlNamedRepository[AttributeId, AttributeDefinition]):
    kind = "attribute"
    table = t.attribute_definitions

    async def _entities(self, rows: Sequence[Row[Any]]) -> list[AttributeDefinition]:
        scopes = await self._children(
            t.attribute_document_types, "attribute_id", (row.id for row in rows)
        )
        return [
            AttributeDefinition(
                id=AttributeId(row.id),
                name=row.name,
                data_type=AttributeType(row.data_type),
                document_type_ids=(
                    None
                    if row.is_global
                    else frozenset(DocumentTypeId(s.document_type_id) for s in scopes[row.id])
                ),
                choices=tuple(row.choices),
                created_at=row.created_at,
                version=row.version,
            )
            for row in rows
        ]

    def _values(self, entity: AttributeDefinition) -> Values:
        return {
            **super()._values(entity),
            "data_type": entity.data_type.value,
            "is_global": entity.document_type_ids is None,
            "choices": list(entity.choices),
        }

    async def _write_children(self, entity: AttributeDefinition) -> None:
        await self._insert_many(
            t.attribute_document_types,
            [
                {"attribute_id": entity.id, "document_type_id": type_id}
                for type_id in entity.document_type_ids or ()
            ],
        )

    async def _delete_children(self, ids: list[AttributeId]) -> None:
        table = t.attribute_document_types
        await self._tx.write(delete(table).where(table.c.attribute_id.in_(ids)))


# --- documents ----------------------------------------------------------------------------------


class SqlDocumentRepository(SqlRepository[DocumentId, Document]):
    kind = "document"
    table = t.documents

    async def find_by_sha256(self, owner: UserId, sha256: Sha256) -> Document | None:
        found = await self._load(
            and_(t.documents.c.owner_id == owner, t.documents.c.sha256 == sha256.hex)
        )
        return found[0] if found else None

    async def list_visible_to(self, user: UserId) -> list[Document]:
        return await self._load(_visible_to(user))

    async def attribute_in_use(
        self,
        attribute: AttributeId,
        *,
        values: Collection[str] | None = None,
        outside_types: Collection[DocumentTypeId] | None = None,
    ) -> bool:
        documents, values_table = t.documents, t.document_attributes
        statement = (
            select(values_table.c.document_id)
            .join(documents, documents.c.id == values_table.c.document_id)
            .where(values_table.c.attribute_id == attribute)
        )
        if values is not None:
            statement = statement.where(values_table.c.value_text.in_(list(values)))
        if outside_types is not None:
            statement = statement.where(
                or_(
                    documents.c.document_type_id.is_(None),
                    documents.c.document_type_id.not_in(list(outside_types)),
                )
            )
        found = await self._tx.read(statement.limit(1))
        return found.first() is not None

    async def query_visible(
        self,
        user: UserId,
        filter: DocumentFilter,
        *,
        before: DocumentId | None = None,
        limit: int,
    ) -> list[Document]:
        return await self._query([_visible_to(user)], filter, before=before, limit=limit)

    async def query(
        self, filter: DocumentFilter, *, before: DocumentId | None = None, limit: int
    ) -> list[Document]:
        return await self._query([], filter, before=before, limit=limit)

    async def _query(
        self,
        criteria: list[ColumnElement[bool]],
        filter: DocumentFilter,
        *,
        before: DocumentId | None,
        limit: int,
    ) -> list[Document]:
        documents = t.documents
        if before is not None:
            criteria.append(documents.c.id < before)
        if filter.contact is not None:
            criteria.append(documents.c.contact_id == filter.contact)
        if filter.document_type is not None:
            criteria.append(documents.c.document_type_id == filter.document_type)
        if filter.drawer is not None:
            criteria.append(documents.c.drawer_id == filter.drawer)
        for tag in sorted(filter.tags):
            tags = t.document_tags
            criteria.append(
                documents.c.id.in_(select(tags.c.document_id).where(tags.c.tag_id == tag))
            )
        if filter.lanes is not None:
            values = [lane.value for lane in filter.lanes if lane is not None]
            lane_criteria = [documents.c.lane.in_(values)]
            if None in filter.lanes:
                lane_criteria.append(documents.c.lane.is_(None))
            criteria.append(or_(*lane_criteria))
        rows = (
            await self._tx.read(
                select(documents).where(*criteria).order_by(documents.c.id.desc()).limit(limit)
            )
        ).all()
        return await self._entities(rows)

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
        documents = t.documents
        criteria: list[ColumnElement[bool]] = []
        if owner is not None:
            criteria.append(documents.c.owner_id == owner)
        if drawer is not None:
            criteria.append(documents.c.drawer_id == drawer)
        if contact is not None:
            criteria.append(documents.c.contact_id == contact)
        if document_type is not None:
            criteria.append(documents.c.document_type_id == document_type)
        if tag is not None:
            tags = t.document_tags
            criteria.append(
                documents.c.id.in_(select(tags.c.document_id).where(tags.c.tag_id == tag))
            )
        if attribute is not None:
            values = t.document_attributes
            criteria.append(
                documents.c.id.in_(
                    select(values.c.document_id).where(values.c.attribute_id == attribute)
                )
            )
        if sha256 is not None:
            criteria.append(documents.c.sha256 == sha256.hex)
        if not criteria:
            raise ValueError("exists needs at least one criterion")
        found = await self._tx.read(select(documents.c.id).where(*criteria).limit(1))
        return found.first() is not None

    async def _entities(self, rows: Sequence[Row[Any]]) -> list[Document]:
        ids = [row.id for row in rows]
        tags = await self._children(t.document_tags, "document_id", ids)
        attributes = await self._children(t.document_attributes, "document_id", ids)
        return [
            Document(
                id=DocumentId(row.id),
                owner_id=UserId(row.owner_id),
                drawer_id=DrawerId(row.drawer_id),
                sha256=Sha256(row.sha256),
                title=row.title,
                original_filename=row.original_filename,
                media_type=row.media_type,
                contact_id=None if row.contact_id is None else ContactId(row.contact_id),
                document_type_id=(
                    None if row.document_type_id is None else DocumentTypeId(row.document_type_id)
                ),
                tag_ids={TagId(tag.tag_id) for tag in tags[row.id]},
                attributes={
                    AttributeId(value.attribute_id): _attribute_value(value)
                    for value in attributes[row.id]
                },
                document_date=row.document_date,
                channel=Channel(row.channel),
                lane=None if row.lane is None else Lane(row.lane),
                processing=Processing(
                    status=ProcessingStatus(row.processing_status),
                    current_step=None if row.processing_step is None else Step(row.processing_step),
                    run=row.processing_run,
                    outcomes={
                        Step(step): Outcome(outcome)
                        for step, outcome in row.processing_outcomes.items()
                    },
                ),
                created_at=row.created_at,
                updated_at=row.updated_at,
                version=row.version,
            )
            for row in rows
        ]

    def _values(self, entity: Document) -> Values:
        processing = entity.processing
        return {
            "id": entity.id,
            "owner_id": entity.owner_id,
            "drawer_id": entity.drawer_id,
            "sha256": entity.sha256.hex,
            "title": entity.title,
            "original_filename": entity.original_filename,
            "media_type": entity.media_type,
            "contact_id": entity.contact_id,
            "document_type_id": entity.document_type_id,
            "document_date": entity.document_date,
            "channel": entity.channel.value,
            "lane": None if entity.lane is None else entity.lane.value,
            "processing_status": processing.status.value,
            "processing_step": (
                None if processing.current_step is None else processing.current_step.value
            ),
            "processing_run": processing.run,
            "processing_outcomes": {
                step.value: outcome.value for step, outcome in processing.outcomes.items()
            },
            "created_at": entity.created_at,
            "updated_at": entity.updated_at,
            "version": entity.version,
        }

    async def _write_children(self, entity: Document) -> None:
        await self._insert_many(
            t.document_tags,
            [{"document_id": entity.id, "tag_id": tag_id} for tag_id in entity.tag_ids],
        )
        await self._insert_many(
            t.document_attributes,
            [
                {"document_id": entity.id, "attribute_id": attribute_id, **_value_columns(value)}
                for attribute_id, value in entity.attributes.items()
            ],
        )

    async def _delete_children(self, ids: list[DocumentId]) -> None:
        for table in (t.document_tags, t.document_attributes):
            await self._tx.write(delete(table).where(table.c.document_id.in_(ids)))


_EMPTY_VALUE: Values = {
    "value_text": None,
    "value_decimal": None,
    "value_currency": None,
    "value_date": None,
    "value_boolean": None,
}


def _value_columns(value: AttributeValue) -> Values:
    """The columns of `document_attributes` that hold an attribute value."""
    match value:
        case bool():
            columns: Values = {"kind": "boolean", "value_boolean": value}
        case Money(amount=amount, currency=currency):
            columns = {"kind": "money", "value_decimal": amount, "value_currency": currency}
        case Decimal():
            columns = {"kind": "decimal", "value_decimal": value}
        case Url(value=url):
            columns = {"kind": "url", "value_text": url}
        case str():
            columns = {"kind": "text", "value_text": value}
        case _:  # date; datetime is no attribute value
            columns = {"kind": "date", "value_date": value}
    return {**_EMPTY_VALUE, **columns}


def _attribute_value(row: Row[Any]) -> AttributeValue:
    match row.kind:
        case "boolean":
            return bool(row.value_boolean)
        case "money":
            return Money(row.value_decimal, row.value_currency)
        case "decimal":
            return row.value_decimal  # type: ignore[no-any-return]
        case "url":
            return Url(row.value_text)
        case "text":
            return row.value_text  # type: ignore[no-any-return]
        case "date":
            return row.value_date  # type: ignore[no-any-return]
    raise ValueError(f"unknown attribute value kind {row.kind!r}")


# --- processing log -----------------------------------------------------------------------------


class SqlProcessingLog:
    def __init__(self, transaction: Transaction) -> None:
        self._tx = transaction

    async def append(self, run: StepRun) -> None:
        result = run.result
        await self._tx.write(
            insert(t.processing_log).values(
                document_id=run.document_id,
                step=run.step.value,
                run=run.run,
                outcome=result.outcome.value,
                reason=result.reason,
                confidence=result.confidence,
                model_version=result.model_version,
                input=result.input,
                output=result.output,
                pipeline_version=run.pipeline_version,
                started_at=run.started_at,
                duration_us=run.duration // _MICROSECOND,
            )
        )

    async def list_for(self, document: DocumentId) -> list[StepRun]:
        log = t.processing_log
        rows = await self._tx.read(
            select(log).where(log.c.document_id == document).order_by(log.c.seq)
        )
        return [_step_run(row) for row in rows]


def _step_run(row: Row[Any]) -> StepRun:
    return StepRun(
        document_id=DocumentId(row.document_id),
        step=Step(row.step),
        run=row.run,
        result=StepResult(
            outcome=Outcome(row.outcome),
            reason=row.reason,
            confidence=row.confidence,
            model_version=row.model_version,
            input=row.input,
            output=row.output,
        ),
        pipeline_version=row.pipeline_version,
        started_at=row.started_at,
        duration=row.duration_us * _MICROSECOND,
    )
