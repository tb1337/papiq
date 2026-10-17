"""Documents with their field values, and the processing log."""

from collections.abc import Collection, Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    ColumnElement,
    FromClause,
    Row,
    and_,
    case,
    delete,
    distinct,
    func,
    insert,
    or_,
    select,
)

from papiq.adapters.outbound.sql import tables as t
from papiq.adapters.outbound.sql.repositories import MICROSECOND, SqlRepository, Values
from papiq.adapters.outbound.sql.spaces import visible_to
from papiq.adapters.outbound.sql.transaction import Transaction
from papiq.core.domain.documents import Channel, Document, Sha256
from papiq.core.domain.fields import FieldValue, Money, Url
from papiq.core.domain.ids import (
    ContactId,
    DocumentId,
    DocumentTypeId,
    FieldId,
    SpaceId,
    TagId,
    UserId,
)
from papiq.core.domain.periods import Period
from papiq.core.domain.pipeline import (
    Lane,
    Node,
    NodeResult,
    NodeRun,
    NodeState,
    NodeStatus,
    Outcome,
    Processing,
    ProcessingStatus,
)
from papiq.core.domain.sorting import sort_key
from papiq.core.domain.verdict import KnownLabels
from papiq.core.ports.repository import (
    NEWEST_FIRST,
    CountBy,
    DocumentFilter,
    DocumentOrder,
    DocumentPosition,
    SortKey,
)

_COUNTED = 200
"""Keys of `count_tagged` per query (a column each)."""


class SqlDocumentRepository(SqlRepository[DocumentId, Document]):
    kind = "document"
    table = t.documents

    async def find_by_sha256(self, owner: UserId, sha256: Sha256) -> Document | None:
        found = await self._load(
            and_(t.documents.c.owner_id == owner, t.documents.c.sha256 == sha256.hex)
        )
        return found[0] if found else None

    async def list_visible_to(self, user: UserId) -> list[Document]:
        return await self._load(visible_to(user))

    async def field_in_use(
        self,
        field: FieldId,
        *,
        values: Collection[str] | None = None,
        outside_types: Collection[DocumentTypeId] | None = None,
    ) -> bool:
        documents, values_table = t.documents, t.document_fields
        statement = (
            select(values_table.c.document_id)
            .join(documents, documents.c.id == values_table.c.document_id)
            .where(values_table.c.field_id == field)
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
        order: DocumentOrder = NEWEST_FIRST,
        after: DocumentPosition | None = None,
        limit: int,
    ) -> list[Document]:
        return await self._query([visible_to(user)], filter, order=order, after=after, limit=limit)

    async def query(
        self,
        filter: DocumentFilter,
        *,
        order: DocumentOrder = NEWEST_FIRST,
        after: DocumentPosition | None = None,
        limit: int,
    ) -> list[Document]:
        return await self._query([], filter, order=order, after=after, limit=limit)

    async def count_by(self, by: CountBy, user: UserId | None) -> dict[UUID, int]:
        documents = t.documents
        criteria: list[ColumnElement[bool]] = [] if user is None else [visible_to(user)]
        column: ColumnElement[Any]
        source: FromClause
        if by in (CountBy.TAG, CountBy.FIELD):
            table = t.document_tags if by is CountBy.TAG else t.document_fields
            column = table.c.tag_id if by is CountBy.TAG else table.c.field_id
            source = table.join(documents, documents.c.id == table.c.document_id)
        else:
            source = documents
            column = documents.c.contact_id
            if by is CountBy.DOCUMENT_TYPE:
                column = documents.c.document_type_id
            elif by is CountBy.TOPIC:
                column = documents.c.topic_id
            criteria.append(column.is_not(None))
        statement = (
            select(column.label("key"), func.count().label("documents"))
            .select_from(source)
            .where(*criteria)
            .group_by(column)
        )
        rows = (await self._tx.read(statement)).all()
        return {UUID(str(row.key)): int(row.documents) for row in rows}

    async def count_tagged(
        self, sets: Mapping[UUID, Collection[TagId]], user: UserId | None
    ) -> dict[UUID, int]:
        """One query per `_COUNTED` keys: each a count of the distinct documents among the
        rows of the key's tags."""
        documents, tags = t.documents, t.document_tags
        criteria: list[ColumnElement[bool]] = [] if user is None else [visible_to(user)]
        keys = list(sets)
        counts: dict[UUID, int] = {}
        for start in range(0, len(keys), _COUNTED):
            batch = keys[start : start + _COUNTED]
            columns = [
                func.count(
                    distinct(case((tags.c.tag_id.in_(sorted(sets[key])), tags.c.document_id)))
                ).label(f"key_{number}")
                for number, key in enumerate(batch)
            ]
            statement = (
                select(*columns)
                .select_from(tags.join(documents, documents.c.id == tags.c.document_id))
                .where(*criteria)
            )
            row = (await self._tx.read(statement)).one()
            counts.update(
                (key, int(row[number])) for number, key in enumerate(batch) if row[number]
            )
        return counts

    async def _query(
        self,
        criteria: list[ColumnElement[bool]],
        filter: DocumentFilter,
        *,
        order: DocumentOrder,
        after: DocumentPosition | None,
        limit: int,
    ) -> list[Document]:
        documents = t.documents
        sorting, behind = _sorting(order, after)
        if behind is not None:
            criteria.append(behind)
        if filter.contact is not None:
            criteria.append(documents.c.contact_id == filter.contact)
        if filter.document_type is not None:
            criteria.append(documents.c.document_type_id == filter.document_type)
        if filter.topics is not None:
            criteria.append(documents.c.topic_id.in_(sorted(filter.topics)))
        if filter.space is not None:
            criteria.append(documents.c.space_id == filter.space)
        for tag_set in filter.tags:
            tags = t.document_tags
            criteria.append(
                documents.c.id.in_(
                    select(tags.c.document_id).where(tags.c.tag_id.in_(sorted(tag_set)))
                )
            )
        for column, groups, owner in (
            (documents.c.contact_id, filter.contact_groups, t.contact_groups.c.contact_id),
            (
                documents.c.document_type_id,
                filter.document_type_groups,
                t.document_type_groups.c.document_type_id,
            ),
        ):
            for group_set in groups:
                in_groups = owner.table.c.tag_id.in_(sorted(group_set))
                criteria.append(column.in_(select(owner).where(in_groups)))
        if filter.confirmed is not None:
            confirmed = documents.c.confirmed_at
            criteria.append(confirmed.is_not(None) if filter.confirmed else confirmed.is_(None))
        if filter.lanes is not None:
            values = [lane.value for lane in filter.lanes if lane is not None]
            lane_criteria = [documents.c.lane.in_(values)]
            if None in filter.lanes:
                lane_criteria.append(documents.c.lane.is_(None))
            criteria.append(or_(*lane_criteria))
        if filter.field is not None:
            fields = t.document_fields
            criteria.append(
                documents.c.id.in_(
                    select(fields.c.document_id).where(fields.c.field_id == filter.field)
                )
            )
        rows = (
            await self._tx.read(select(documents).where(*criteria).order_by(*sorting).limit(limit))
        ).all()
        return await self._entities(rows)

    async def count_confirmed(self, *, after: datetime | None = None) -> int:
        confirmed = t.documents.c.confirmed_at
        statement = select(func.count()).select_from(t.documents).where(confirmed.is_not(None))
        if after is not None:
            statement = statement.where(confirmed > after)
        return int((await self._tx.read(statement)).scalar_one())

    async def confirmed_labels(self, reader: UserId) -> KnownLabels:
        documents, tags = t.documents, t.document_tags
        confirmed = documents.c.confirmed_at.is_not(None)
        readable = visible_to(reader)

        async def values(column: Any, source: Any) -> list[Any]:
            statement = (
                select(column)
                .select_from(source)
                .where(confirmed, readable, column.is_not(None))
                .distinct()
            )
            return [row[0] for row in await self._tx.read(statement)]

        tagged = documents.join(tags, tags.c.document_id == documents.c.id)
        return KnownLabels(
            contacts=frozenset(map(ContactId, await values(documents.c.contact_id, documents))),
            document_types=frozenset(
                map(DocumentTypeId, await values(documents.c.document_type_id, documents))
            ),
            tags=frozenset(map(TagId, await values(tags.c.tag_id, tagged))),
            topics=frozenset(map(TagId, await values(documents.c.topic_id, documents))),
        )

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
        documents = t.documents
        criteria: list[ColumnElement[bool]] = []
        if owner is not None:
            criteria.append(documents.c.owner_id == owner)
        if space is not None:
            criteria.append(documents.c.space_id == space)
        if contact is not None:
            criteria.append(documents.c.contact_id == contact)
        if document_type is not None:
            criteria.append(documents.c.document_type_id == document_type)
        if topic is not None:
            criteria.append(documents.c.topic_id == topic)
        if tag is not None:
            tags = t.document_tags
            criteria.append(
                documents.c.id.in_(select(tags.c.document_id).where(tags.c.tag_id == tag))
            )
        if field is not None:
            values = t.document_fields
            criteria.append(
                documents.c.id.in_(select(values.c.document_id).where(values.c.field_id == field))
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
        fields = await self._children(t.document_fields, "document_id", ids)
        return [
            Document(
                id=DocumentId(row.id),
                owner_id=UserId(row.owner_id),
                space_id=SpaceId(row.space_id),
                sha256=Sha256(row.sha256),
                title=row.title,
                original_filename=row.original_filename,
                media_type=row.media_type,
                contact_id=None if row.contact_id is None else ContactId(row.contact_id),
                document_type_id=(
                    None if row.document_type_id is None else DocumentTypeId(row.document_type_id)
                ),
                topic_id=None if row.topic_id is None else TagId(row.topic_id),
                tag_ids={TagId(tag.tag_id) for tag in tags[row.id]},
                fields={FieldId(value.field_id): _field_value(value) for value in fields[row.id]},
                document_date=row.document_date,
                channel=Channel(row.channel),
                lane=None if row.lane is None else Lane(row.lane),
                page_count=row.page_count,
                confirmed_at=row.confirmed_at,
                processing=Processing(
                    status=ProcessingStatus(row.processing_status),
                    run=row.processing_run,
                    nodes={
                        Node(node): _node_state(state)
                        for node, state in row.processing_nodes.items()
                    },
                    vision=row.processing_vision,
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
            "space_id": entity.space_id,
            "sha256": entity.sha256.hex,
            "title": entity.title,
            "title_key": sort_key(entity.title),
            "original_filename": entity.original_filename,
            "media_type": entity.media_type,
            "contact_id": entity.contact_id,
            "document_type_id": entity.document_type_id,
            "topic_id": entity.topic_id,
            "document_date": entity.document_date,
            "channel": entity.channel.value,
            "lane": None if entity.lane is None else entity.lane.value,
            "page_count": entity.page_count,
            "confirmed_at": entity.confirmed_at,
            "processing_status": processing.status.value,
            "processing_run": processing.run,
            "processing_nodes": {
                node.value: _node_json(state) for node, state in processing.nodes.items()
            },
            "processing_vision": processing.vision,
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
            t.document_fields,
            [
                {"document_id": entity.id, "field_id": field_id, **_value_columns(value)}
                for field_id, value in entity.fields.items()
            ],
        )

    async def _delete_children(self, ids: list[DocumentId]) -> None:
        for table in (t.document_tags, t.document_fields):
            await self._tx.write(delete(table).where(table.c.document_id.in_(ids)))


_EMPTY_VALUE: Values = {
    "value_text": None,
    "value_decimal": None,
    "value_currency": None,
    "value_date": None,
    "value_date_end": None,
    "value_boolean": None,
}


def _value_columns(value: FieldValue) -> Values:
    """The columns of `document_fields` that hold a field value."""
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
        case Period(start=start, end=end):
            columns = {"kind": "period", "value_date": start, "value_date_end": end}
        case date():  # datetime is no field value
            columns = {"kind": "date", "value_date": value}
    return {**_EMPTY_VALUE, **columns}


def _field_value(row: Row[Any]) -> FieldValue:
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
        case "period":
            return Period(row.value_date, row.value_date_end)
    raise ValueError(f"unknown field value kind {row.kind!r}")


class SqlProcessingLog:
    def __init__(self, transaction: Transaction) -> None:
        self._tx = transaction

    async def append(self, run: NodeRun) -> None:
        result = run.result
        await self._tx.write(
            insert(t.processing_log).values(
                document_id=run.document_id,
                node=run.node.value,
                run=run.run,
                outcome=result.outcome.value,
                reason=result.reason,
                confidence=result.confidence,
                model_version=result.model_version,
                input=result.input,
                output=result.output,
                pipeline_version=run.pipeline_version,
                started_at=run.started_at,
                duration_us=run.duration // MICROSECOND,
            )
        )

    async def list_for(self, document: DocumentId) -> list[NodeRun]:
        log = t.processing_log
        rows = await self._tx.read(
            select(log).where(log.c.document_id == document).order_by(log.c.seq)
        )
        return [_node_run(row) for row in rows]


def _node_run(row: Row[Any]) -> NodeRun:
    return NodeRun(
        document_id=DocumentId(row.document_id),
        node=Node(row.node),
        run=row.run,
        result=NodeResult(
            outcome=Outcome(row.outcome),
            reason=row.reason,
            confidence=row.confidence,
            model_version=row.model_version,
            input=row.input,
            output=row.output,
        ),
        pipeline_version=row.pipeline_version,
        started_at=row.started_at,
        duration=row.duration_us * MICROSECOND,
    )


def _node_json(state: NodeState) -> dict[str, Any]:
    return {
        "status": state.status.value,
        "outcome": None if state.outcome is None else state.outcome.value,
        "started_at": _iso(state.started_at),
        "finished_at": _iso(state.finished_at),
    }


def _node_state(data: dict[str, Any]) -> NodeState:
    outcome = data.get("outcome")
    return NodeState(
        status=NodeStatus(data["status"]),
        outcome=None if outcome is None else Outcome(outcome),
        started_at=_from_iso(data.get("started_at")),
        finished_at=_from_iso(data.get("finished_at")),
    )


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _from_iso(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


def _sorting(
    order: DocumentOrder, after: DocumentPosition | None
) -> tuple[list[ColumnElement[Any]], ColumnElement[bool] | None]:
    """The ORDER BY of `order`, and the criterion for the rows after `after` in it. Ties go
    by id; documents without a date come last in both directions, so the criterion for a
    position with a date also takes every row without one."""
    documents = t.documents
    id_ = documents.c.id
    ascending = order.ascending

    def beyond(column: ColumnElement[Any], value: object) -> ColumnElement[bool]:
        return column > value if ascending else column < value

    def direction(column: ColumnElement[Any]) -> ColumnElement[Any]:
        return column.asc() if ascending else column.desc()

    match order.key:
        case SortKey.CREATED:
            behind = None if after is None else beyond(id_, after.id)
            return [direction(id_)], behind
        case SortKey.TITLE:
            key = documents.c.title_key
            behind = None
            if after is not None:
                behind = or_(
                    beyond(key, after.value), and_(key == after.value, beyond(id_, after.id))
                )
            return [direction(key), direction(id_)], behind
        case SortKey.DOCUMENT_DATE:
            day = documents.c.document_date
            behind = None
            if after is not None and after.value is None:
                behind = and_(day.is_(None), beyond(id_, after.id))
            elif after is not None:
                behind = or_(
                    beyond(day, after.value),
                    and_(day == after.value, beyond(id_, after.id)),
                    day.is_(None),
                )
            return [day.is_(None).asc(), direction(day), direction(id_)], behind
