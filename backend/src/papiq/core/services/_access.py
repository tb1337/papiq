"""Loading entities together with the caller's rights."""

from dataclasses import replace

from papiq.core.domain.documents import Document, DocumentChanges, Unset
from papiq.core.domain.errors import (
    AuthenticationError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from papiq.core.domain.ids import ContactId, DocumentId, SpaceId, UserId
from papiq.core.domain.master_data import TagScope
from papiq.core.domain.permissions import (
    can_file_into,
    can_read_document,
    can_write_document,
    space_access,
)
from papiq.core.domain.pipeline import space_chooser
from papiq.core.domain.spaces import Space
from papiq.core.domain.tag_tree import TagTree
from papiq.core.domain.users import User
from papiq.core.ports import UnitOfWork


async def load_actor(uow: UnitOfWork, actor: UserId) -> User:
    """The caller; AuthenticationError if the account is gone or deactivated (it may have
    changed since the request was authenticated)."""
    user = await uow.users.find(actor)
    if user is None or not user.active:
        raise AuthenticationError("authentication is required")
    return user


async def readable_document(uow: UnitOfWork, user: User, id: DocumentId) -> tuple[Document, Space]:
    document = await uow.documents.find(id)
    if document is None:
        raise NotFoundError("document", id)
    space = await uow.spaces.get(document.space_id)
    if not can_read_document(user, document, space):
        raise NotFoundError("document", id)
    return document, space


async def writable_document(uow: UnitOfWork, user: User, id: DocumentId) -> tuple[Document, Space]:
    document, space = await readable_document(uow, user, id)
    if not can_write_document(user, document, space):
        raise PermissionDeniedError(f"no write access to document {id}")
    return document, space


async def visible_space(uow: UnitOfWork, user: User, id: SpaceId) -> Space:
    space = await uow.spaces.find(id)
    if space is None or space_access(user, space) is None:
        raise NotFoundError("space", id)
    return space


async def may_file(uow: UnitOfWork, document: Document, space: Space) -> bool:
    """Whether filing may put the document into `space`: its owner may write to the space, or
    an active admin put the document there by hand (admins file into any space)."""
    owner = await uow.users.get(document.owner_id)
    if can_file_into(owner, space):
        return True
    chooser = space_chooser(await uow.processing_log.list_for(document.id), space.id)
    if chooser is None:
        return False
    user = await uow.users.find(UserId(chooser))
    return user is not None and user.is_active_admin


def contact_topics_lock(contact: ContactId) -> str:
    """The lock (`UnitOfWork.lock`) a unit takes before it reads the topics a contact enables
    to give a document of the contact a topic, or to take topics away from the contact: one
    waits for the other and sees what it did."""
    return f"contact-topics:{contact}"


async def checked_changes(
    uow: UnitOfWork,
    changes: DocumentChanges,
    document: Document | None = None,
    actor: User | None = None,
) -> DocumentChanges:
    """The change as it is applied to `document` (None: a new one): its tags normalised (a
    child and its parent set together leave the child, `TagTree.normalise`), and its topic one
    the contact after the change enables.

    A contact given that does not enable the document's topic clears the topic. A topic given
    that the contact does not enable is enabled at the contact, in `uow`, if `actor` is an
    active admin; otherwise, and for a topic without a contact, it is a ValidationError. A
    topic the document keeps or gets is checked under `contact_topics_lock`: the unit must
    not have written yet.
    NotFoundError if a contact, type, tag or topic it refers to does not exist,
    ValidationError for a tag that does not describe documents (a group, a topic) or a topic
    that is none."""
    if not isinstance(changes.contact_id, Unset) and changes.contact_id is not None:
        await uow.contacts.get(changes.contact_id)
    if not isinstance(changes.document_type_id, Unset) and changes.document_type_id is not None:
        await uow.document_types.get(changes.document_type_id)
    topic_given = not isinstance(changes.topic_id, Unset)
    if isinstance(changes.tag_ids, Unset) and not topic_given:
        return await _fitted_topic(uow, changes, document, actor)
    tree = TagTree.of(await uow.tags.list_all())
    if not isinstance(changes.tag_ids, Unset):
        for id in changes.tag_ids:
            tag = tree.get(id)
            if tag.scope is not TagScope.DOCUMENT:
                raise ValidationError(f"'{tag.name}' is a {_SCOPES[tag.scope]}, not a document tag")
        changes = replace(changes, tag_ids=tree.normalise(changes.tag_ids))
    if not isinstance(changes.topic_id, Unset) and changes.topic_id is not None:
        topic = tree.get(changes.topic_id)
        if topic.scope is not TagScope.TOPIC:
            raise ValidationError(f"'{topic.name}' is no topic")
    return await _fitted_topic(uow, changes, document, actor)


_SCOPES = {
    TagScope.CONTACT: "contact group",
    TagScope.DOCUMENT_TYPE: "document type group",
    TagScope.TOPIC: "topic",
}


async def _fitted_topic(
    uow: UnitOfWork, changes: DocumentChanges, document: Document | None, actor: User | None
) -> DocumentChanges:
    """`changes` with the topic the contact after it enables (see `checked_changes`); only a
    change of the contact or the topic looks at it."""
    contact_given = not isinstance(changes.contact_id, Unset)
    topic_given = not isinstance(changes.topic_id, Unset)
    if not (contact_given or topic_given):
        return changes
    current = document.contact_id if document is not None else None
    contact_id = changes.contact_id if not isinstance(changes.contact_id, Unset) else current
    topic_id = changes.topic_id if not isinstance(changes.topic_id, Unset) else None
    if not topic_given and document is not None:
        topic_id = document.topic_id
    if topic_id is None:
        return changes
    if contact_id is None:
        if topic_given:
            raise ValidationError("a topic is one of the contact's: give the document a contact")
        return replace(changes, topic_id=None)
    await uow.lock(contact_topics_lock(contact_id))
    contact = await uow.contacts.get(contact_id)
    if topic_id in contact.topic_ids:
        return changes
    if not topic_given:
        return replace(changes, topic_id=None)  # the new contact does not enable it
    if actor is not None and actor.is_active_admin:
        contact.enable_topic(topic_id)
        await uow.contacts.update(contact)
        return changes
    topic = await uow.tags.get(topic_id)
    raise ValidationError(
        f"'{contact.name}' does not enable the topic '{topic.name}' (an admin enables it at "
        "the contact, or by choosing it)"
    )
