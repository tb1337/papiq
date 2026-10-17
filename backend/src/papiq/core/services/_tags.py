"""Tags as filters and counts: what a tag, a group or a topic stands for in a query of
documents.

A filter on a tag matches its descendants; a filter on a group matches the documents whose
contact (type) is in the group or a group below it; a filter on a topic the documents with it or
a topic below it. The repository and the index take sets ("one of these") and know nothing of
the trees (`DocumentFilter`).
"""

from collections.abc import Mapping, Sequence
from dataclasses import replace
from uuid import UUID

from papiq.core.domain.documents import Document
from papiq.core.domain.ids import ContactId, DocumentTypeId, TagId, UserId
from papiq.core.domain.master_data import Contact, DocumentType, TagScope
from papiq.core.domain.tag_tree import TagTree
from papiq.core.ports import CountBy, DocumentFilter, UnitOfWork
from papiq.core.ports.repository import TagSets


def expand(filter: DocumentFilter, tree: TagTree) -> DocumentFilter:
    """`filter` with each set of tags and groups widened by their descendants."""

    def widened(sets: TagSets) -> TagSets:
        return tuple(tree.with_descendants(tags) for tags in sets)

    return replace(
        filter,
        tags=widened(filter.tags),
        contact_groups=widened(filter.contact_groups),
        document_type_groups=widened(filter.document_type_groups),
        topics=None if filter.topics is None else tree.with_descendants(filter.topics),
    )


async def expanded(uow: UnitOfWork, filter: DocumentFilter) -> DocumentFilter:
    if not (filter.tags or filter.contact_groups or filter.document_type_groups or filter.topics):
        return filter
    return expand(filter, TagTree.of(await uow.tags.list_all()))


async def group_lookup(
    uow: UnitOfWork, filter: DocumentFilter
) -> tuple[Mapping[ContactId, frozenset[TagId]], Mapping[DocumentTypeId, frozenset[TagId]]]:
    """The groups of each contact and type, as far as `filter` asks for them."""
    contacts: dict[ContactId, frozenset[TagId]] = {}
    types: dict[DocumentTypeId, frozenset[TagId]] = {}
    if filter.contact_groups:
        contacts = {item.id: item.group_ids for item in await uow.contacts.list_all()}
    if filter.document_type_groups:
        types = {item.id: item.group_ids for item in await uow.document_types.list_all()}
    return contacts, types


def matches(
    document: Document,
    filter: DocumentFilter,
    contacts: Mapping[ContactId, frozenset[TagId]],
    types: Mapping[DocumentTypeId, frozenset[TagId]],
) -> bool:
    """The (expanded) filter as the repository applies it; `contacts` and `types`: their
    groups (`group_lookup`)."""
    contact = contacts.get(document.contact_id) if document.contact_id else None
    kind = types.get(document.document_type_id) if document.document_type_id else None
    return (
        (filter.contact is None or document.contact_id == filter.contact)
        and (filter.document_type is None or document.document_type_id == filter.document_type)
        and (filter.topics is None or document.topic_id in filter.topics)
        and all(tags & document.tag_ids for tags in filter.tags)
        and all(groups & (contact or frozenset()) for groups in filter.contact_groups)
        and all(groups & (kind or frozenset()) for groups in filter.document_type_groups)
        and (filter.space is None or document.space_id == filter.space)
        and (filter.lanes is None or document.lane in filter.lanes)
    )


async def tag_counts(uow: UnitOfWork, user: UserId | None) -> dict[UUID, int]:
    """How many documents the user may read (None: every document) each tag stands for: a
    document tag counts each document once that carries it or a tag below it; a group the
    documents whose contact (type) is in it or a group below it; a topic those with it or a
    topic below it (a document has one topic at most). Only tags with documents."""
    tree = TagTree.of(await uow.tags.list_all())
    counts = dict(await uow.documents.count_by(CountBy.TAG, user))
    parents: dict[UUID, frozenset[TagId]] = {
        tag.id: tree.with_descendants([tag.id])
        for tag in tree.of_scope(TagScope.DOCUMENT)
        if tree.children.get(tag.id)
    }
    for parent in parents:
        counts.pop(parent, None)
    if parents:
        counts.update(await uow.documents.count_tagged(parents, user))
    for scope, by in (
        (TagScope.CONTACT, CountBy.CONTACT),
        (TagScope.DOCUMENT_TYPE, CountBy.DOCUMENT_TYPE),
    ):
        groups = tree.of_scope(scope)
        if not groups:
            continue
        members: Sequence[Contact | DocumentType] = (
            await uow.contacts.list_all()
            if scope is TagScope.CONTACT
            else await uow.document_types.list_all()
        )
        per_member = await uow.documents.count_by(by, user)
        for group in groups:
            below = tree.with_descendants([group.id])
            total = sum(
                per_member.get(member.id, 0) for member in members if member.group_ids & below
            )
            if total:
                counts[group.id] = total
    topics = tree.of_scope(TagScope.TOPIC)
    if topics:
        per_topic = await uow.documents.count_by(CountBy.TOPIC, user)
        for topic in topics:
            total = sum(per_topic.get(id, 0) for id in tree.with_descendants([topic.id]))
            if total:
                counts[topic.id] = total
    return counts
