"""Permission rules. Rights hang on spaces, never on single documents.

- The owner of a document reads and writes it.
- Other users see a document only through its space: as space owner (read and write) or through
  a share (`read` or `read_write`), and only once it is green. Yellow and red documents and
  documents in processing are visible to their owner only.
- Filing into a space needs write access to that space.
- Moving a document to another space: its owner, into a space the owner may write to; or an
  admin. Shares never allow moving.
- Deleting, retrying and reprocessing a document, reading its processing log and deciding its
  open fields is up to its owner; managing a space (rename, share, delete) is up to its owner.
- Admins have every right and see everything (Tobi, 08.10.2026): they read and write every
  document in every lane and in processing, control it as its owner does, file into and manage
  every space, and manage users and master data. Only admins do the latter.
- A deactivated user has no rights at all, a deactivated admin included.

What a user reaches without the admin's rights (`reach_access`, `in_reach`) is what lists show by
default and what webhooks report: own documents, and green documents in spaces the user owns or
that are shared with them.
"""

from papiq.core.domain.documents import Document
from papiq.core.domain.pipeline import Lane
from papiq.core.domain.spaces import ShareLevel, Space
from papiq.core.domain.users import User


def space_access(user: User, space: Space) -> ShareLevel | None:
    if user.is_active_admin:
        return ShareLevel.READ_WRITE
    return reach_space_access(user, space)


def reach_space_access(user: User, space: Space) -> ShareLevel | None:
    """As `space_access`, without the rights of an admin."""
    if not user.active:
        return None
    if space.owner_id == user.id:
        return ShareLevel.READ_WRITE
    return space.shares.get(user.id)


def document_access(user: User, document: Document, space: Space) -> ShareLevel | None:
    """Access of `user` to `document`, which lies in `space`."""
    access = reach_access(user, document, space)
    if access is None and user.is_active_admin:
        return ShareLevel.READ_WRITE
    return access


def reach_access(user: User, document: Document, space: Space) -> ShareLevel | None:
    """As `document_access`, without the rights of an admin."""
    if document.space_id != space.id:
        raise ValueError(f"document {document.id} is not in space {space.id}")
    if not user.active:
        return None
    if document.owner_id == user.id:
        return ShareLevel.READ_WRITE
    if document.lane is not Lane.GREEN:
        return None
    return reach_space_access(user, space)


def can_read_document(user: User, document: Document, space: Space) -> bool:
    return document_access(user, document, space) is not None


def in_reach(user: User, document: Document, space: Space) -> bool:
    """`can_read_document` without the rights of an admin."""
    return reach_access(user, document, space) is not None


def can_write_document(user: User, document: Document, space: Space) -> bool:
    access = document_access(user, document, space)
    return access is not None and access.can_write


def can_file_into(user: User, space: Space) -> bool:
    access = space_access(user, space)
    return access is not None and access.can_write


def can_move_document(user: User, document: Document, target: Space) -> bool:
    if user.is_active_admin:
        return True
    return is_document_owner(user, document) and can_file_into(user, target)


def can_manage_space(user: User, space: Space) -> bool:
    """Rename, share and delete a space."""
    return user.active and (space.owner_id == user.id or user.is_admin)


def is_document_owner(user: User, document: Document) -> bool:
    return user.active and document.owner_id == user.id


def can_control_document(user: User, document: Document) -> bool:
    """Delete, retry and reprocess a document, read its processing log, review and confirm it."""
    return is_document_owner(user, document) or user.is_active_admin


def can_manage_master_data(user: User) -> bool:
    return user.is_active_admin


def can_manage_users(user: User) -> bool:
    return user.is_active_admin
