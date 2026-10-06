from papiq.core.domain.documents import Document, DocumentChanges, Unset
from papiq.core.domain.errors import NotFoundError, PermissionDeniedError
from papiq.core.domain.ids import DocumentId, DrawerId, UserId
from papiq.core.domain.permissions import can_move_document, is_document_owner
from papiq.core.domain.pipeline import StepRun
from papiq.core.ports import Clock, UnitOfWork, UnitOfWorkFactory
from papiq.core.services._access import (
    load_actor,
    readable_document,
    visible_drawer,
    writable_document,
)


class DocumentService:
    """Reading and changing documents. Ingest and processing live in PipelineService."""

    def __init__(self, uow: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    async def get(self, actor: UserId, id: DocumentId) -> Document:
        async with self._uow() as uow:
            document, _ = await readable_document(uow, await load_actor(uow, actor), id)
            return document

    async def list_visible(self, actor: UserId) -> list[Document]:
        async with self._uow() as uow:
            await load_actor(uow, actor)
            return await uow.documents.list_visible_to(actor)

    async def processing_log(self, actor: UserId, id: DocumentId) -> list[StepRun]:
        async with self._uow() as uow:
            await readable_document(uow, await load_actor(uow, actor), id)
            return await uow.processing_log.list_for(id)

    async def update_metadata(
        self, actor: UserId, id: DocumentId, changes: DocumentChanges
    ) -> Document:
        """Needs write access. Referenced contact, type, tags and attributes must exist."""
        async with self._uow() as uow:
            document, _ = await writable_document(uow, await load_actor(uow, actor), id)
            await _check_references(uow, changes)
            definitions = {item.id: item for item in await uow.attributes.list_all()}
            document.apply_changes(changes, definitions, self._clock.now())
            await _save(uow, document)
            await uow.commit()
        return document

    async def move(self, actor: UserId, id: DocumentId, drawer: DrawerId) -> None:
        """The owner moves into a drawer they may write to; an admin moves any document into
        any drawer. Moving grants the admin no read access, so nothing is returned."""
        async with self._uow() as uow:
            user = await load_actor(uow, actor)
            if user.is_admin:
                document = await uow.documents.get(id)
                target = await uow.drawers.get(drawer)
            else:
                document, _ = await readable_document(uow, user, id)
                if not is_document_owner(user, document):
                    raise PermissionDeniedError(f"only the owner moves document {id}")
                target = await visible_drawer(uow, user, drawer)
            if not can_move_document(user, document, target):
                raise PermissionDeniedError(f"no write access to drawer {drawer}")
            document.move_to(target.id, self._clock.now())
            await _save(uow, document)
            await uow.commit()

    async def delete(self, actor: UserId, id: DocumentId) -> None:
        """Owner only. Removes the metadata; stored files stay (cleanup is a later job)."""
        async with self._uow() as uow:
            user = await load_actor(uow, actor)
            document, _ = await readable_document(uow, user, id)
            if not is_document_owner(user, document):
                raise PermissionDeniedError(f"only the owner deletes document {id}")
            document.delete(self._clock.now())
            await uow.documents.remove(id)
            await uow.outbox.add(document.pull_events())
            await uow.commit()


async def _save(uow: UnitOfWork, document: Document) -> None:
    await uow.documents.update(document)
    await uow.outbox.add(document.pull_events())


async def _check_references(uow: UnitOfWork, changes: DocumentChanges) -> None:
    if not isinstance(changes.contact_id, Unset) and changes.contact_id is not None:
        await uow.contacts.get(changes.contact_id)
    if not isinstance(changes.document_type_id, Unset) and changes.document_type_id is not None:
        await uow.document_types.get(changes.document_type_id)
    if not isinstance(changes.tag_ids, Unset):
        for tag in changes.tag_ids:
            if await uow.tags.find(tag) is None:
                raise NotFoundError("tag", tag)
