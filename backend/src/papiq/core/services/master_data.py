from collections.abc import Awaitable, Callable, Collection
from datetime import datetime
from typing import Any

from papiq.core.domain.attributes import AttributeDefinition, AttributeType
from papiq.core.domain.errors import ConflictError, PermissionDeniedError
from papiq.core.domain.ids import AttributeId, ContactId, DocumentTypeId, TagId, UserId
from papiq.core.domain.master_data import Contact, DocumentType, MasterData, Tag
from papiq.core.domain.permissions import can_manage_master_data
from papiq.core.ports import Clock, NamedRepository, UnitOfWork, UnitOfWorkFactory
from papiq.core.services._access import load_actor

type Repo[E] = Callable[[UnitOfWork], NamedRepository[Any, E]]


def _contacts(uow: UnitOfWork) -> NamedRepository[ContactId, Contact]:
    return uow.contacts


def _document_types(uow: UnitOfWork) -> NamedRepository[DocumentTypeId, DocumentType]:
    return uow.document_types


def _tags(uow: UnitOfWork) -> NamedRepository[TagId, Tag]:
    return uow.tags


def _attributes(uow: UnitOfWork) -> NamedRepository[AttributeId, AttributeDefinition]:
    return uow.attributes


class MasterDataService:
    """Contacts, document types, tags and attribute definitions. Changes are admin-only."""

    def __init__(self, uow: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    async def create_contact(self, actor: UserId, name: str) -> Contact:
        return await self._create(actor, _contacts, Contact.create(name=name, now=self._now()))

    async def rename_contact(self, actor: UserId, id: ContactId, name: str) -> Contact:
        return await self._rename(actor, _contacts, id, name)

    async def create_document_type(self, actor: UserId, name: str) -> DocumentType:
        item = DocumentType.create(name=name, now=self._now())
        return await self._create(actor, _document_types, item)

    async def rename_document_type(
        self, actor: UserId, id: DocumentTypeId, name: str
    ) -> DocumentType:
        return await self._rename(actor, _document_types, id, name)

    async def create_tag(self, actor: UserId, name: str) -> Tag:
        return await self._create(actor, _tags, Tag.create(name=name, now=self._now()))

    async def rename_tag(self, actor: UserId, id: TagId, name: str) -> Tag:
        return await self._rename(actor, _tags, id, name)

    async def create_attribute(
        self,
        actor: UserId,
        name: str,
        data_type: AttributeType,
        *,
        document_type_ids: Collection[DocumentTypeId] | None = None,
        choices: Collection[str] = (),
    ) -> AttributeDefinition:
        """`document_type_ids=None` creates a global attribute."""
        definition = AttributeDefinition.create(
            name=name,
            data_type=data_type,
            now=self._now(),
            document_type_ids=document_type_ids,
            choices=choices,
        )

        async def check(uow: UnitOfWork) -> None:
            for document_type in definition.document_type_ids or ():
                await uow.document_types.get(document_type)

        return await self._create(actor, _attributes, definition, check)

    async def rename_attribute(
        self, actor: UserId, id: AttributeId, name: str
    ) -> AttributeDefinition:
        return await self._rename(actor, _attributes, id, name)

    def _now(self) -> datetime:
        return self._clock.now()

    async def _create[E: MasterData](
        self,
        actor: UserId,
        repository: Repo[E],
        item: E,
        check: Callable[[UnitOfWork], Awaitable[None]] | None = None,
    ) -> E:
        async with self._uow() as uow:
            await _require_admin(uow, actor)
            if check is not None:
                await check(uow)
            await _check_name_free(repository(uow), item)
            await repository(uow).add(item)
            await uow.commit()
        return item

    async def _rename[E: MasterData](
        self, actor: UserId, repository: Repo[E], id: Any, name: str
    ) -> E:
        async with self._uow() as uow:
            await _require_admin(uow, actor)
            item = await repository(uow).get(id)
            item.rename(name)
            await _check_name_free(repository(uow), item)
            await repository(uow).update(item)
            await uow.commit()
        return item


async def _require_admin(uow: UnitOfWork, actor: UserId) -> None:
    if not can_manage_master_data(await load_actor(uow, actor)):
        raise PermissionDeniedError("only admins change master data")


async def _check_name_free[E: MasterData](repository: NamedRepository[Any, E], item: E) -> None:
    existing = await repository.find_by_name(item.name)
    # Every kind of master data has an `id`; the base class leaves its type to the kinds.
    if existing is not None and getattr(existing, "id") != getattr(item, "id"):  # noqa: B009
        raise ConflictError(f"'{item.name}' already exists")
