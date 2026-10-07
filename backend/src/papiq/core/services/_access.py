"""Loading entities together with the caller's rights."""

from papiq.core.domain.documents import Document, DocumentChanges, Unset
from papiq.core.domain.drawers import Drawer
from papiq.core.domain.errors import AuthenticationError, NotFoundError, PermissionDeniedError
from papiq.core.domain.ids import DocumentId, DrawerId, UserId
from papiq.core.domain.permissions import (
    can_read_document,
    can_write_document,
    drawer_access,
)
from papiq.core.domain.users import User
from papiq.core.ports import UnitOfWork


async def load_actor(uow: UnitOfWork, actor: UserId) -> User:
    """The caller; AuthenticationError if the account is gone or deactivated (it may have
    changed since the request was authenticated)."""
    user = await uow.users.find(actor)
    if user is None or not user.active:
        raise AuthenticationError("authentication is required")
    return user


async def readable_document(uow: UnitOfWork, user: User, id: DocumentId) -> tuple[Document, Drawer]:
    document = await uow.documents.find(id)
    if document is None:
        raise NotFoundError("document", id)
    drawer = await uow.drawers.get(document.drawer_id)
    if not can_read_document(user, document, drawer):
        raise NotFoundError("document", id)
    return document, drawer


async def writable_document(uow: UnitOfWork, user: User, id: DocumentId) -> tuple[Document, Drawer]:
    document, drawer = await readable_document(uow, user, id)
    if not can_write_document(user, document, drawer):
        raise PermissionDeniedError(f"no write access to document {id}")
    return document, drawer


async def visible_drawer(uow: UnitOfWork, user: User, id: DrawerId) -> Drawer:
    drawer = await uow.drawers.find(id)
    if drawer is None or drawer_access(user, drawer) is None:
        raise NotFoundError("drawer", id)
    return drawer


async def check_references(uow: UnitOfWork, changes: DocumentChanges) -> None:
    """NotFoundError if a contact, type or tag the change refers to does not exist."""
    if not isinstance(changes.contact_id, Unset) and changes.contact_id is not None:
        await uow.contacts.get(changes.contact_id)
    if not isinstance(changes.document_type_id, Unset) and changes.document_type_id is not None:
        await uow.document_types.get(changes.document_type_id)
    if not isinstance(changes.tag_ids, Unset):
        for tag in changes.tag_ids:
            if await uow.tags.find(tag) is None:
                raise NotFoundError("tag", tag)
