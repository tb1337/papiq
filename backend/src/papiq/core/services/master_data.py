from collections.abc import Awaitable, Callable, Collection
from datetime import datetime
from typing import Any

from papiq.core.domain.documents import UNSET, Unset
from papiq.core.domain.errors import ConflictError, PermissionDeniedError
from papiq.core.domain.fields import FieldDefinition, FieldType
from papiq.core.domain.ids import ContactId, DocumentTypeId, FieldId, TagId, UserId
from papiq.core.domain.master_data import Contact, DocumentType, MasterData, Tag
from papiq.core.domain.permissions import can_manage_master_data
from papiq.core.domain.rules import References
from papiq.core.domain.validation import name_key
from papiq.core.ports import Clock, NamedRepository, UnitOfWork, UnitOfWorkFactory
from papiq.core.services._access import load_actor
from papiq.core.services.indexing import REFRESH_JOB
from papiq.core.services.rules.references import disable_rules, misfits, refers_to

type Repo[E] = Callable[[UnitOfWork], NamedRepository[Any, E]]


def _contacts(uow: UnitOfWork) -> NamedRepository[ContactId, Contact]:
    return uow.contacts


def _document_types(uow: UnitOfWork) -> NamedRepository[DocumentTypeId, DocumentType]:
    return uow.document_types


def _tags(uow: UnitOfWork) -> NamedRepository[TagId, Tag]:
    return uow.tags


def _fields(uow: UnitOfWork) -> NamedRepository[FieldId, FieldDefinition]:
    return uow.fields


class MasterDataService:
    """Contacts, document types, tags and field definitions. Changes are admin-only."""

    def __init__(
        self, uow: UnitOfWorkFactory, clock: Clock, *, index_renames: bool = False
    ) -> None:
        """`index_renames`: renaming a contact, type or tag queues a job that brings the search
        index of the documents that carry it up to date (set when there is a search index)."""
        self._uow = uow
        self._clock = clock
        self._index_renames = index_renames

    async def create_contact(
        self, actor: UserId, name: str, aliases: Collection[str] = ()
    ) -> Contact:
        item = Contact.create(name=name, now=self._now(), aliases=aliases)
        return await self._create(
            actor, _contacts, item, lambda uow: _check_contact_names(uow, item)
        )

    async def rename_contact(self, actor: UserId, id: ContactId, name: str) -> Contact:
        return await self.change_contact(actor, id, name=name)

    async def change_contact(
        self,
        actor: UserId,
        id: ContactId,
        *,
        name: str | None = None,
        aliases: Collection[str] | None = None,
    ) -> Contact:
        """`None` leaves the name or the aliases as they are; `aliases` replaces all of them."""

        def change(item: Contact) -> None:
            if name is not None:
                item.rename(name)
            if aliases is not None:
                item.set_aliases(aliases)

        return await self._change(
            actor,
            _contacts,
            id,
            change,
            check=lambda uow, item: _check_contact_names(uow, item),
            index_as="contact" if name is not None else None,
        )

    async def create_document_type(
        self, actor: UserId, name: str, description: str | None = None
    ) -> DocumentType:
        item = DocumentType.create(name=name, now=self._now(), description=description)
        return await self._create(actor, _document_types, item)

    async def rename_document_type(
        self, actor: UserId, id: DocumentTypeId, name: str
    ) -> DocumentType:
        return await self.change_document_type(actor, id, name=name)

    async def change_document_type(
        self,
        actor: UserId,
        id: DocumentTypeId,
        *,
        name: str | None = None,
        description: str | Unset | None = UNSET,
    ) -> DocumentType:
        """`None` (name) or UNSET (description) leaves it as it is; a description of None
        removes it."""

        def change(item: DocumentType) -> None:
            if name is not None:
                item.rename(name)
            if not isinstance(description, Unset):
                item.describe(description)

        return await self._change(
            actor,
            _document_types,
            id,
            change,
            index_as="document_type" if name is not None else None,
        )

    async def create_tag(self, actor: UserId, name: str) -> Tag:
        return await self._create(actor, _tags, Tag.create(name=name, now=self._now()))

    async def rename_tag(self, actor: UserId, id: TagId, name: str) -> Tag:
        return await self._rename(actor, _tags, id, name, index_as="tag")

    async def create_field(
        self,
        actor: UserId,
        name: str,
        data_type: FieldType,
        *,
        document_type_ids: Collection[DocumentTypeId] | None = None,
        choices: Collection[str] = (),
    ) -> FieldDefinition:
        """`document_type_ids=None` creates a global field."""
        definition = FieldDefinition.create(
            name=name,
            data_type=data_type,
            now=self._now(),
            document_type_ids=document_type_ids,
            choices=choices,
        )

        async def check(uow: UnitOfWork) -> None:
            for document_type in definition.document_type_ids or ():
                await uow.document_types.get(document_type)

        return await self._create(actor, _fields, definition, check)

    async def change_field(
        self,
        actor: UserId,
        id: FieldId,
        *,
        name: str | None = None,
        choices: Collection[str] | None = None,
        document_type_ids: Collection[DocumentTypeId] | Unset | None = UNSET,
    ) -> FieldDefinition:
        """Admins only. Name; choices (adding is free, removing only what no document uses);
        scope (`None`: global; widening is free, narrowing only while no document outside the
        new scope has a value). The data type never changes. ConflictError if values are in
        use."""
        async with self._uow() as uow:
            await _require_admin(uow, actor)
            definition = await uow.fields.get(id)
            if name is not None:
                definition.rename(name)
                await _check_name_free(uow.fields, definition)
            if choices is not None:
                removed = definition.change_choices(choices)
                if removed and await uow.documents.field_in_use(id, values=removed):
                    raise ConflictError(f"documents use the choices {', '.join(sorted(removed))}")
            if not isinstance(document_type_ids, Unset):
                for document_type in document_type_ids or ():
                    await uow.document_types.get(document_type)
                narrower = definition.change_scope(document_type_ids)
                scope = definition.document_type_ids
                if (
                    narrower
                    and scope is not None
                    and await uow.documents.field_in_use(id, outside_types=scope)
                ):
                    raise ConflictError("documents outside the new scope have values")
            await uow.fields.update(definition)
            if choices is not None:
                fields = {item.id: item for item in await uow.fields.list_all()}
                fields[definition.id] = definition
                await disable_rules(
                    uow,
                    self._now(),
                    f"field '{definition.name}' no longer allows a value the rule uses",
                    misfits(definition.id, fields),
                )
            await uow.commit()
        return definition

    async def rename_field(self, actor: UserId, id: FieldId, name: str) -> FieldDefinition:
        return await self._rename(actor, _fields, id, name)

    # --- reading (every signed-in user) ----------------------------------------------------------

    async def list_contacts(self, actor: UserId) -> list[Contact]:
        return await self._list(actor, _contacts)

    async def list_document_types(self, actor: UserId) -> list[DocumentType]:
        return await self._list(actor, _document_types)

    async def list_tags(self, actor: UserId) -> list[Tag]:
        return await self._list(actor, _tags)

    async def list_fields(self, actor: UserId) -> list[FieldDefinition]:
        return await self._list(actor, _fields)

    async def get_contact(self, actor: UserId, id: ContactId) -> Contact:
        return await self._get(actor, _contacts, id)

    async def get_document_type(self, actor: UserId, id: DocumentTypeId) -> DocumentType:
        return await self._get(actor, _document_types, id)

    async def get_tag(self, actor: UserId, id: TagId) -> Tag:
        return await self._get(actor, _tags, id)

    async def get_field(self, actor: UserId, id: FieldId) -> FieldDefinition:
        return await self._get(actor, _fields, id)

    # --- deleting (admins; only what no document uses) -------------------------------------------

    async def delete_contact(self, actor: UserId, id: ContactId) -> None:
        await self._delete(
            actor,
            _contacts,
            id,
            lambda uow: uow.documents.exists(contact=id),
            ("contact", lambda refs: refs.contacts),
        )

    async def delete_document_type(self, actor: UserId, id: DocumentTypeId) -> None:
        async def used(uow: UnitOfWork) -> bool:
            if await uow.documents.exists(document_type=id):
                return True
            return any(
                id in (field.document_type_ids or ()) for field in await uow.fields.list_all()
            )

        await self._delete(
            actor, _document_types, id, used, ("document type", lambda refs: refs.document_types)
        )

    async def delete_tag(self, actor: UserId, id: TagId) -> None:
        await self._delete(
            actor,
            _tags,
            id,
            lambda uow: uow.documents.exists(tag=id),
            ("tag", lambda refs: refs.tags),
        )

    async def delete_field(self, actor: UserId, id: FieldId) -> None:
        await self._delete(
            actor,
            _fields,
            id,
            lambda uow: uow.documents.exists(field=id),
            ("field", lambda refs: refs.fields),
        )

    async def _list[E: MasterData](self, actor: UserId, repository: Repo[E]) -> list[E]:
        async with self._uow() as uow:
            await load_actor(uow, actor)
            items = await repository(uow).list_all()
        return sorted(items, key=lambda item: item.name.casefold())

    async def _get[E: MasterData](self, actor: UserId, repository: Repo[E], id: Any) -> E:
        async with self._uow() as uow:
            await load_actor(uow, actor)
            return await repository(uow).get(id)

    async def _delete[E: MasterData](
        self,
        actor: UserId,
        repository: Repo[E],
        id: Any,
        used: Callable[[UnitOfWork], Awaitable[bool]],
        referenced: tuple[str, Callable[[References], frozenset[Any]]],
    ) -> None:
        """Only what no document uses; rules that refer to it are disabled."""
        kind, select = referenced
        async with self._uow() as uow:
            await _require_admin(uow, actor)
            item = await repository(uow).get(id)
            if await used(uow):
                raise ConflictError(f"'{item.name}' is in use")
            await repository(uow).remove(id)
            await disable_rules(
                uow, self._now(), f"{kind} '{item.name}' was deleted", refers_to(select, id)
            )
            await uow.commit()

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
        self,
        actor: UserId,
        repository: Repo[E],
        id: Any,
        name: str,
        *,
        index_as: str | None = None,
    ) -> E:
        return await self._change(
            actor, repository, id, lambda item: item.rename(name), index_as=index_as
        )

    async def _change[E: MasterData](
        self,
        actor: UserId,
        repository: Repo[E],
        id: Any,
        change: Callable[[E], None],
        *,
        check: Callable[[UnitOfWork, E], Awaitable[None]] | None = None,
        index_as: str | None = None,
    ) -> E:
        """`index_as`: the kind whose documents' search index is brought up to date."""
        async with self._uow() as uow:
            await _require_admin(uow, actor)
            item = await repository(uow).get(id)
            change(item)
            await _check_name_free(repository(uow), item)
            if check is not None:
                await check(uow, item)
            await repository(uow).update(item)
            if index_as is not None and self._index_renames:
                await uow.jobs.enqueue(
                    REFRESH_JOB, {"kind": index_as, "id": str(id)}, run_at=self._clock.now()
                )
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


async def _check_contact_names(uow: UnitOfWork, contact: Contact) -> None:
    """No name or alias of the contact is a name or an alias of another contact."""
    for other in await uow.contacts.list_all():
        if other.id == contact.id:
            continue
        for name in contact.names():
            if other.is_named(name):
                raise ConflictError(f"'{name}' is already a name of the contact '{other.name}'")


async def learn_alias(uow: UnitOfWork, contact_id: ContactId, alias: str) -> Contact | None:
    """Make `alias` an alias of the contact, in `uow` without committing: a person chose the
    contact for a document in which the language model read `alias`. Nothing is learned if it
    is a name or an alias of the contact already, or the name of another contact. An alias of
    another contact moves (the person's choice is the newer word). The contact with the new
    alias, or None."""
    alias = alias.strip()
    contacts = await uow.contacts.list_all()
    target = next((item for item in contacts if item.id == contact_id), None)
    if not alias or target is None or target.is_named(alias):
        return None
    for other in contacts:
        if other.id == contact_id or not other.is_named(alias):
            continue
        if name_key(other.name) == name_key(alias):
            return None
        other.set_aliases(item for item in other.aliases if name_key(item) != name_key(alias))
        await uow.contacts.update(other)
    target.set_aliases([*target.aliases, alias])
    await uow.contacts.update(target)
    return target
