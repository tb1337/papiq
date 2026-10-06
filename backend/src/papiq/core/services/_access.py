"""Loading entities together with the caller's rights."""

from papiq.core.domain.documents import Document
from papiq.core.domain.drawers import Drawer
from papiq.core.domain.errors import NotFoundError, PermissionDeniedError
from papiq.core.domain.ids import DocumentId, DrawerId, UserId
from papiq.core.domain.permissions import (
    can_read_document,
    can_write_document,
    drawer_access,
)
from papiq.core.domain.users import User
from papiq.core.ports import UnitOfWork


async def load_actor(uow: UnitOfWork, actor: UserId) -> User:
    return await uow.users.get(actor)


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
