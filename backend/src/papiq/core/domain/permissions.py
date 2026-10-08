"""Permission rules. Rights hang on drawers, never on single documents.

- The owner of a document reads and writes it.
- Other users see a document only through its drawer: as drawer owner (read and write) or through
  a share (`read` or `read_write`), and only once it is green. Yellow and red documents and
  documents in processing are visible to their owner only.
- Filing into a drawer needs write access to that drawer.
- Moving a document to another drawer: its owner, into a drawer the owner may write to; or an
  admin. Shares never allow moving.
- Deleting, retrying and reprocessing a document, reading its processing log and deciding its
  open fields is up to its owner; managing a drawer (rename, share, delete) is up to its owner.
- Admins have every right and see everything (Tobi, 08.10.2026): they read and write every
  document in every lane and in processing, control it as its owner does, file into and manage
  every drawer, and manage users and master data. Only admins do the latter.
- A deactivated user has no rights at all, a deactivated admin included.

What a user reaches without the admin's rights (`reach_access`, `in_reach`) is what lists show by
default and what webhooks report: own documents, and green documents in drawers the user owns or
that are shared with them.
"""

from papiq.core.domain.documents import Document
from papiq.core.domain.drawers import Drawer, ShareLevel
from papiq.core.domain.pipeline import Lane
from papiq.core.domain.users import User


def drawer_access(user: User, drawer: Drawer) -> ShareLevel | None:
    if user.is_active_admin:
        return ShareLevel.READ_WRITE
    return reach_drawer_access(user, drawer)


def reach_drawer_access(user: User, drawer: Drawer) -> ShareLevel | None:
    """As `drawer_access`, without the rights of an admin."""
    if not user.active:
        return None
    if drawer.owner_id == user.id:
        return ShareLevel.READ_WRITE
    return drawer.shares.get(user.id)


def document_access(user: User, document: Document, drawer: Drawer) -> ShareLevel | None:
    """Access of `user` to `document`, which lies in `drawer`."""
    access = reach_access(user, document, drawer)
    if access is None and user.is_active_admin:
        return ShareLevel.READ_WRITE
    return access


def reach_access(user: User, document: Document, drawer: Drawer) -> ShareLevel | None:
    """As `document_access`, without the rights of an admin."""
    if document.drawer_id != drawer.id:
        raise ValueError(f"document {document.id} is not in drawer {drawer.id}")
    if not user.active:
        return None
    if document.owner_id == user.id:
        return ShareLevel.READ_WRITE
    if document.lane is not Lane.GREEN:
        return None
    return reach_drawer_access(user, drawer)


def can_read_document(user: User, document: Document, drawer: Drawer) -> bool:
    return document_access(user, document, drawer) is not None


def in_reach(user: User, document: Document, drawer: Drawer) -> bool:
    """`can_read_document` without the rights of an admin."""
    return reach_access(user, document, drawer) is not None


def can_write_document(user: User, document: Document, drawer: Drawer) -> bool:
    access = document_access(user, document, drawer)
    return access is not None and access.can_write


def can_file_into(user: User, drawer: Drawer) -> bool:
    access = drawer_access(user, drawer)
    return access is not None and access.can_write


def can_move_document(user: User, document: Document, target: Drawer) -> bool:
    if user.is_active_admin:
        return True
    return is_document_owner(user, document) and can_file_into(user, target)


def can_manage_drawer(user: User, drawer: Drawer) -> bool:
    """Rename, share and delete a drawer."""
    return user.active and (drawer.owner_id == user.id or user.is_admin)


def is_document_owner(user: User, document: Document) -> bool:
    return user.active and document.owner_id == user.id


def can_control_document(user: User, document: Document) -> bool:
    """Delete, retry and reprocess a document, read its processing log, review and confirm it."""
    return is_document_owner(user, document) or user.is_active_admin


def can_manage_master_data(user: User) -> bool:
    return user.is_active_admin


def can_manage_users(user: User) -> bool:
    return user.is_active_admin
