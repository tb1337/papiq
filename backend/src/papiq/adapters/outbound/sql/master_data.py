"""Contacts, document types, tags and field definitions: named master data."""

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import Row, and_, delete

from papiq.adapters.outbound.sql import tables as t
from papiq.adapters.outbound.sql.repositories import SqlRepository, Values
from papiq.core.domain.appearance import ContactLogo, FailedLogoFetch, LogoSource
from papiq.core.domain.fields import FieldDefinition, FieldType
from papiq.core.domain.identifiers import Identifier, IdentifierKind, IdentifierSource
from papiq.core.domain.ids import ContactId, DocumentTypeId, FieldId, TagId
from papiq.core.domain.master_data import (
    Assignment,
    Contact,
    DocumentType,
    MasterData,
    Tag,
    TagScope,
)
from papiq.core.domain.validation import name_key


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

    def _values(self, entity: Contact) -> Values:
        logo, failure = entity.logo, entity.logo_failure
        return super()._values(entity) | {
            "logo_source": None if logo is None else logo.source.value,
            "logo_domain": None if logo is None else logo.domain,
            "logo_stored_at": None if logo is None else logo.stored_at,
            "logo_failed_domains": None if failure is None else " ".join(sorted(failure.domains)),
            "logo_failed_at": None if failure is None else failure.failed_at,
        }

    async def _entities(self, rows: Sequence[Row[Any]]) -> list[Contact]:
        ids = [row.id for row in rows]
        aliases = await self._children(t.contact_aliases, "contact_id", ids)
        identifiers = await self._children(t.contact_identifiers, "contact_id", ids)
        groups = await self._children(t.contact_groups, "contact_id", ids)
        topics = await self._children(t.contact_topics, "contact_id", ids)
        return [
            Contact(
                id=ContactId(row.id),
                name=row.name,
                aliases=[
                    alias.name for alias in sorted(aliases[row.id], key=lambda item: item.position)
                ],
                identifiers=[
                    Identifier(
                        IdentifierKind(item.kind),
                        item.value,
                        IdentifierSource(item.source),
                        None if item.topic_id is None else TagId(item.topic_id),
                    )
                    for item in identifiers[row.id]
                ],
                group_ids=frozenset(TagId(group.tag_id) for group in groups[row.id]),
                topic_ids=frozenset(TagId(topic.tag_id) for topic in topics[row.id]),
                logo=_logo(row),
                logo_failure=(
                    None
                    if row.logo_failed_domains is None
                    else FailedLogoFetch(
                        domains=frozenset(row.logo_failed_domains.split()),
                        failed_at=row.logo_failed_at,
                    )
                ),
                created_at=row.created_at,
                version=row.version,
            )
            for row in rows
        ]

    async def _write_children(self, entity: Contact) -> None:
        await self._insert_many(
            t.contact_aliases,
            [
                {
                    "contact_id": entity.id,
                    "name": alias,
                    "name_key": name_key(alias),
                    "position": position,
                }
                for position, alias in enumerate(entity.aliases)
            ],
        )
        await self._insert_many(
            t.contact_identifiers,
            [
                {
                    "contact_id": entity.id,
                    "kind": item.kind.value,
                    "value": item.value,
                    "source": item.source.value,
                    "topic_id": item.topic_id,
                }
                for item in entity.identifiers
            ],
        )
        await self._insert_many(
            t.contact_groups,
            [{"contact_id": entity.id, "tag_id": group} for group in entity.group_ids],
        )
        await self._insert_many(
            t.contact_topics,
            [{"contact_id": entity.id, "tag_id": topic} for topic in entity.topic_ids],
        )

    async def _delete_children(self, ids: list[ContactId]) -> None:
        tables = (t.contact_aliases, t.contact_identifiers, t.contact_groups, t.contact_topics)
        for table in tables:
            await self._tx.write(delete(table).where(table.c.contact_id.in_(ids)))


class SqlDocumentTypeRepository(SqlNamedRepository[DocumentTypeId, DocumentType]):
    kind = "document type"
    table = t.document_types

    def _values(self, entity: DocumentType) -> Values:
        return super()._values(entity) | {
            "description": entity.description,
            "title_pattern": entity.title_pattern,
            "icon": entity.icon,
        }

    async def _entities(self, rows: Sequence[Row[Any]]) -> list[DocumentType]:
        groups = await self._children(
            t.document_type_groups, "document_type_id", (row.id for row in rows)
        )
        return [
            DocumentType(
                id=DocumentTypeId(row.id),
                name=row.name,
                description=row.description,
                title_pattern=row.title_pattern,
                group_ids=frozenset(TagId(group.tag_id) for group in groups[row.id]),
                icon=row.icon,
                created_at=row.created_at,
                version=row.version,
            )
            for row in rows
        ]

    async def _write_children(self, entity: DocumentType) -> None:
        await self._insert_many(
            t.document_type_groups,
            [{"document_type_id": entity.id, "tag_id": group} for group in entity.group_ids],
        )

    async def _delete_children(self, ids: list[DocumentTypeId]) -> None:
        table = t.document_type_groups
        await self._tx.write(delete(table).where(table.c.document_type_id.in_(ids)))


class SqlTagRepository(SqlNamedRepository[TagId, Tag]):
    kind = "tag"
    table = t.tags

    async def find_by_name(self, name: str, scope: TagScope = TagScope.DOCUMENT) -> Tag | None:
        """Names are unique per scope."""
        found = await self._load(
            and_(self.table.c.name_key == name_key(name), self.table.c.scope == scope.value)
        )
        return found[0] if found else None

    def _values(self, entity: Tag) -> Values:
        return super()._values(entity) | {
            "scope": entity.scope.value,
            "parent_id": entity.parent_id,
            "assignment": entity.assignment.value,
            "shown": entity.shown,
            "icon": entity.icon,
            "color": entity.color,
        }

    def _entity(self, row: Row[Any]) -> Tag:
        return Tag(
            id=TagId(row.id),
            name=row.name,
            scope=TagScope(row.scope),
            parent_id=None if row.parent_id is None else TagId(row.parent_id),
            assignment=Assignment(row.assignment),
            shown=row.shown,
            icon=row.icon,
            color=row.color,
            created_at=row.created_at,
            version=row.version,
        )


def _logo(row: Row[Any]) -> ContactLogo | None:
    if row.logo_source is None:
        return None
    return ContactLogo(
        source=LogoSource(row.logo_source), stored_at=row.logo_stored_at, domain=row.logo_domain
    )


class SqlFieldRepository(SqlNamedRepository[FieldId, FieldDefinition]):
    kind = "field"
    table = t.field_definitions

    async def _entities(self, rows: Sequence[Row[Any]]) -> list[FieldDefinition]:
        scopes = await self._children(t.field_document_types, "field_id", (row.id for row in rows))
        return [
            FieldDefinition(
                id=FieldId(row.id),
                name=row.name,
                data_type=FieldType(row.data_type),
                document_type_ids=(
                    None
                    if row.is_global
                    else frozenset(DocumentTypeId(s.document_type_id) for s in scopes[row.id])
                ),
                choices=tuple(row.choices),
                required=row.is_global and row.required,
                required_types=frozenset(
                    DocumentTypeId(s.document_type_id)
                    for s in scopes[row.id]
                    if s.required and not row.is_global
                ),
                description=row.description,
                assignment=Assignment(row.assignment),
                created_at=row.created_at,
                version=row.version,
            )
            for row in rows
        ]

    def _values(self, entity: FieldDefinition) -> Values:
        return {
            **super()._values(entity),
            "data_type": entity.data_type.value,
            "is_global": entity.document_type_ids is None,
            "required": entity.required,
            "choices": list(entity.choices),
            "description": entity.description,
            "assignment": entity.assignment.value,
        }

    async def _write_children(self, entity: FieldDefinition) -> None:
        await self._insert_many(
            t.field_document_types,
            [
                {
                    "field_id": entity.id,
                    "document_type_id": type_id,
                    "required": type_id in entity.required_types,
                }
                for type_id in entity.document_type_ids or ()
            ],
        )

    async def _delete_children(self, ids: list[FieldId]) -> None:
        table = t.field_document_types
        await self._tx.write(delete(table).where(table.c.field_id.in_(ids)))
