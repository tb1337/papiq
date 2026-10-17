"""Master data: contacts, document types and tags. Global, no owner; only admins change them.

A tag has a scope (`TagScope`): it describes a document, groups contacts or document types, or
is a topic. Tags nest (`parent_id`, a tag of the same scope); only the tag set is stored, its
ancestors are derived (`tag_tree.TagTree`). A contact and a document type are in any number of
groups (`group_ids`), which their documents inherit. A contact enables topics (`topic_ids`), of
which a document of the contact carries one at most. Tags and document types carry an icon,
tags a colour, contacts a logo (`appearance`)."""

import re
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from typing import Self

from papiq.core.domain.appearance import (
    ContactLogo,
    FailedLogoFetch,
    checked_color,
    checked_icon,
)
from papiq.core.domain.errors import ValidationError
from papiq.core.domain.identifiers import (
    MAX_IDENTIFIERS,
    Identifier,
    IdentifierKind,
    IdentifierSource,
)
from papiq.core.domain.ids import ContactId, DocumentTypeId, TagId, new_id
from papiq.core.domain.validation import name_key, require_name, require_utc

MAX_ALIASES = 100
"""Aliases of one contact."""
MAX_DESCRIPTION = 300
"""Characters of the description of a document type or a field; it goes into the prompts."""
MAX_GROUPS = 100
"""Groups of one contact or document type."""
MAX_TOPICS = 100
"""Topics one contact enables."""


class Assignment(StrEnum):
    """Who sets a value: Papiq's classification (`auto`: the signals propose it, the model is
    asked for it) or only a person or a rule (`manual`: values no document shows, such as the
    number stamped on the paper copy)."""

    AUTO = "auto"
    MANUAL = "manual"


class TagScope(StrEnum):
    """What a tag describes, fixed when it is made: a document (`document`, as tags were
    before 0.3), contacts (`contact`: a contact group, "insurance"), document types
    (`document_type`: a type group, "invoice" over "medical bill") or what a document is about
    with its contact (`topic`: "work", "health insurance"; a global catalogue, enabled at
    contacts, at most one per document). Names are unique per scope."""

    DOCUMENT = "document"
    CONTACT = "contact"
    DOCUMENT_TYPE = "document_type"
    TOPIC = "topic"

    @property
    def is_group(self) -> bool:
        """Whether its tags group contacts or document types: static, set by hand only, and
        inherited by documents through their contact and type."""
        return self in (TagScope.CONTACT, TagScope.DOCUMENT_TYPE)

    @property
    def assignment(self) -> Assignment:
        """The assignment a new tag of the scope gets unless another is given."""
        return Assignment.MANUAL if self.is_group else Assignment.AUTO

    @property
    def shown(self) -> bool:
        """Whether a new tag of the scope is shown at documents unless told otherwise: groups
        are not, document tags and topics are."""
        return not self.is_group


@dataclass(kw_only=True)
class MasterData:
    """Common part of all master data: a name unique per kind regardless of case."""

    name: str
    created_at: datetime
    version: int = 1

    def __post_init__(self) -> None:
        self.name = require_name(self.name)
        self.created_at = require_utc(self.created_at, "created_at")

    def rename(self, name: str) -> None:
        self.name = require_name(name)


def _groups(group_ids: Iterable[TagId]) -> frozenset[TagId]:
    groups = frozenset(group_ids)
    if len(groups) > MAX_GROUPS:
        raise ValidationError(f"at most {MAX_GROUPS} groups")
    return groups


def _topics(topic_ids: Iterable[TagId]) -> frozenset[TagId]:
    topics = frozenset(topic_ids)
    if len(topics) > MAX_TOPICS:
        raise ValidationError(f"a contact enables at most {MAX_TOPICS} topics")
    return topics


@dataclass(kw_only=True)
class Contact(MasterData):
    """The other party of a document (Paperless-ngx: correspondent).

    `aliases` are other names the contact is written as in documents ("Nord Krankenversicherung
    AG" for "Nord Versicherungsgruppe"); classification matches them like the name. They are
    unique regardless of case and differ from the name; across contacts, names and aliases are
    unique too (see `MasterDataService`).

    `identifiers` are values its documents carry (see `identifiers`): entered by hand, learned
    by training, or learned and rejected. Each value belongs to one contact at most, and may
    point to one of the contact's topics.

    `group_ids`: the contact groups it is in (tags of scope `contact`; the service checks
    them). `topic_ids`: the topics it enables (tags of scope `topic`, checked by the service):
    a document of the contact carries one of them at most. An identifier's topic is one of
    them (ValidationError otherwise); one taken away leaves the identifiers that point to it
    without a topic.

    `logo`: the stored logo, if any (`appearance.ContactLogo`); `logo_failure`: the domains
    whose fetch found none, kept until a logo is stored or removed (a fetch for one of them
    waits for a person to ask)."""

    id: ContactId
    aliases: list[str] = field(default_factory=list)
    identifiers: list[Identifier] = field(default_factory=list)
    group_ids: frozenset[TagId] = frozenset()
    topic_ids: frozenset[TagId] = frozenset()
    logo: ContactLogo | None = None
    logo_failure: FailedLogoFetch | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        self.aliases = _aliases(self.name, self.aliases)
        self.identifiers = _identifiers(self.identifiers)
        self.group_ids = _groups(self.group_ids)
        self.topic_ids = _topics(self.topic_ids)
        self._check_topics()

    @classmethod
    def create(
        cls,
        *,
        name: str,
        now: datetime,
        aliases: Iterable[str] = (),
        identifiers: Iterable[Identifier] = (),
        topic_ids: Iterable[TagId] = (),
    ) -> Self:
        contact = cls(
            id=ContactId(new_id()),
            name=name,
            created_at=now,
            aliases=list(aliases),
            topic_ids=frozenset(topic_ids),
        )
        contact.set_identifiers(identifiers)
        return contact

    def rename(self, name: str) -> None:
        """An alias that becomes the name is no longer an alias."""
        name = require_name(name)
        self.aliases = [alias for alias in self.aliases if name_key(alias) != name_key(name)]
        self.name = name

    def set_aliases(self, aliases: Iterable[str]) -> None:
        self.aliases = _aliases(self.name, aliases)

    def set_groups(self, group_ids: Iterable[TagId]) -> None:
        self.group_ids = _groups(group_ids)

    def set_topics(self, topic_ids: Iterable[TagId]) -> frozenset[TagId]:
        """Make `topic_ids` the topics it enables; the identifiers that point to one taken away
        lose their topic. Returns the topics taken away (the service refuses that while a
        document of the contact carries one)."""
        topics = _topics(topic_ids)
        removed = self.topic_ids - topics
        self.topic_ids = topics
        if removed:
            self.identifiers = [
                replace(item, topic_id=None) if item.topic_id in removed else item
                for item in self.identifiers
            ]
        return removed

    def enable_topic(self, topic_id: TagId) -> bool:
        """Enable one topic more; whether it was not enabled yet."""
        if topic_id in self.topic_ids:
            return False
        self.topic_ids = _topics(self.topic_ids | {topic_id})
        return True

    def names(self) -> list[str]:
        """The name and the aliases."""
        return [self.name, *self.aliases]

    def set_identifiers(
        self, entered: Iterable[Identifier], rejected: Iterable[Identifier] | None = None
    ) -> None:
        """`entered` replaces the identifiers entered by hand (a learned one entered is then
        one entered by hand), each with its topic (one of the contact's: ValidationError
        otherwise); `rejected`, if given, the rejected ones (a learned one rejected no longer
        counts, and training does not learn it again)."""
        manual = {
            item.key: Identifier(item.kind, item.value, topic_id=item.topic_id) for item in entered
        }
        if rejected is None:
            blocked = {item.key for item in self.identifiers if not item.counts}
        else:
            blocked = {item.key for item in rejected}
        blocked -= manual.keys()
        learned = [
            item
            for item in self.identifiers
            if item.source is IdentifierSource.LEARNED
            and item.key not in manual
            and item.key not in blocked
        ]
        refused = [Identifier(kind, value, IdentifierSource.REJECTED) for kind, value in blocked]
        identifiers = _identifiers([*manual.values(), *learned, *refused])
        self._check_topics(identifiers)
        self.identifiers = identifiers

    def set_learned(self, learned: Iterable[Identifier]) -> None:
        """Replace the learned identifiers, each with its topic if the contact enables it (a
        topic it does not is left out); values entered by hand or rejected stay as they
        are."""
        kept = [item for item in self.identifiers if item.source is not IdentifierSource.LEARNED]
        taken = {item.key for item in kept}
        fresh = [
            Identifier(
                item.kind,
                item.value,
                IdentifierSource.LEARNED,
                item.topic_id if item.topic_id in self.topic_ids else None,
            )
            for item in learned
            if item.key not in taken
        ]
        self.identifiers = _identifiers([*kept, *fresh])

    def drop_identifier(self, key: tuple[IdentifierKind, str]) -> bool:
        """Forget the value (another contact took it); whether it was there."""
        kept = [item for item in self.identifiers if item.key != key]
        changed = len(kept) < len(self.identifiers)
        self.identifiers = kept
        return changed

    def evidence(self) -> list[Identifier]:
        """The identifiers that count: entered and learned."""
        return [item for item in self.identifiers if item.counts]

    @property
    def domains(self) -> list[str]:
        """The domains among the identifiers that count, those entered first: where its website
        is."""
        return [
            item.value
            for item in self.identifiers
            if item.counts and item.kind is IdentifierKind.EMAIL_DOMAIN
        ]

    def set_logo(self, logo: ContactLogo) -> None:
        """A new logo; a failed fetch noted before is past."""
        self.logo = logo
        self.logo_failure = None

    def remove_logo(self) -> None:
        self.logo = None
        self.logo_failure = None

    def note_failed_fetch(self, domains: Iterable[str], at: datetime) -> None:
        """Fetching from `domains` found no logo, at `at`: they join those that failed before;
        a logo it has stays."""
        failed = self.logo_failure.domains if self.logo_failure is not None else frozenset()
        self.logo_failure = FailedLogoFetch(domains=failed | set(domains), failed_at=at)

    def fetch_failed(self, domain: str) -> bool:
        """Whether fetching from `domain` found no logo before."""
        return self.logo_failure is not None and domain in self.logo_failure.domains

    def is_named(self, name: str) -> bool:
        """Whether `name` is the name or an alias, regardless of case."""
        key = name_key(name)
        return any(name_key(own) == key for own in self.names())

    def _check_topics(self, identifiers: Iterable[Identifier] | None = None) -> None:
        for item in self.identifiers if identifiers is None else identifiers:
            if item.topic_id is not None and item.topic_id not in self.topic_ids:
                raise ValidationError(
                    f"the topic of the identifier '{item.value}' is none the contact enables"
                )


@dataclass(kw_only=True)
class DocumentType(MasterData):
    """The kind of a document; field definitions can be bound to it. `description` tells the
    language model what belongs to the type ("pay slips, Entgeltbescheinigung, ...").
    `title_pattern`: a template (Liquid, `domain.templates`) for the title of its documents,
    carried out by a rule the type keeps (`rules.title_rule`); its syntax and fields are the
    service's to check. `group_ids`: the type groups it is in (tags of scope
    `document_type`). `icon`: the name of a Lucide icon (`appearance.checked_icon`)."""

    id: DocumentTypeId
    description: str | None = None
    title_pattern: str | None = None
    group_ids: frozenset[TagId] = frozenset()
    icon: str | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        self.description = checked_description(self.description)
        self.title_pattern = _title_pattern(self.title_pattern)
        self.group_ids = _groups(self.group_ids)
        self.icon = checked_icon(self.icon)

    @classmethod
    def create(
        cls,
        *,
        name: str,
        now: datetime,
        description: str | None = None,
        title_pattern: str | None = None,
        icon: str | None = None,
    ) -> Self:
        return cls(
            id=DocumentTypeId(new_id()),
            name=name,
            created_at=now,
            description=description,
            title_pattern=title_pattern,
            icon=icon,
        )

    def describe(self, description: str | None) -> None:
        self.description = checked_description(description)

    def set_icon(self, icon: str | None) -> None:
        self.icon = checked_icon(icon)

    def set_title_pattern(self, pattern: str | None) -> None:
        self.title_pattern = _title_pattern(pattern)

    def set_groups(self, group_ids: Iterable[TagId]) -> None:
        self.group_ids = _groups(group_ids)


@dataclass(kw_only=True)
class Tag(MasterData):
    """A label; tags carry no permissions.

    `scope` never changes. `parent_id`: the tag it is nested under, of the same scope (the
    tree's rules, no cycle and at most five levels, are `TagTree.check_parent`'s).
    `assignment`: whether the classification proposes and sets it (`auto`, document tags
    only) or only a person or a rule does (`manual`: status tags such as "to pay"; groups
    always). `shown`: whether documents show it (in their chips; filters, search, rules and
    templates see it either way). `icon` and `color`: how its chips look
    (`appearance.checked_icon`, `appearance.checked_color`)."""

    id: TagId
    scope: TagScope = TagScope.DOCUMENT
    parent_id: TagId | None = None
    assignment: Assignment = Assignment.AUTO
    shown: bool = True
    icon: str | None = None
    color: str | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        self.icon = checked_icon(self.icon)
        self.color = checked_color(self.color)
        self._check()

    @classmethod
    def create(
        cls,
        *,
        name: str,
        now: datetime,
        scope: TagScope = TagScope.DOCUMENT,
        parent_id: TagId | None = None,
        assignment: Assignment | None = None,
        shown: bool | None = None,
        icon: str | None = None,
        color: str | None = None,
    ) -> Self:
        """`assignment` and `shown` follow the scope unless given (`TagScope.assignment`,
        `TagScope.shown`)."""
        return cls(
            id=TagId(new_id()),
            name=name,
            created_at=now,
            scope=scope,
            parent_id=parent_id,
            assignment=scope.assignment if assignment is None else assignment,
            shown=scope.shown if shown is None else shown,
            icon=icon,
            color=color,
        )

    def set_icon(self, icon: str | None) -> None:
        self.icon = checked_icon(icon)

    def set_color(self, color: str | None) -> None:
        self.color = checked_color(color)

    def move(self, parent_id: TagId | None) -> None:
        """Nest it under `parent_id` (None: a top-level tag); the caller checks the tree."""
        self.parent_id = parent_id
        self._check()

    def assign(self, assignment: Assignment) -> None:
        self.assignment = assignment
        self._check()

    @property
    def is_target(self) -> bool:
        """Whether the classification proposes and sets it as a tag: a document tag set
        `auto`."""
        return self.scope is TagScope.DOCUMENT and self.assignment is Assignment.AUTO

    @property
    def is_topic_target(self) -> bool:
        """Whether the classification proposes and sets it as a document's topic: a topic set
        `auto` (one set `manual` only a person or a rule sets)."""
        return self.scope is TagScope.TOPIC and self.assignment is Assignment.AUTO

    def _check(self) -> None:
        if self.parent_id == self.id:
            raise ValidationError("a tag cannot be its own parent")
        if self.scope.is_group and self.assignment is not Assignment.MANUAL:
            raise ValidationError("groups are set by hand only (assignment 'manual')")


_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def one_line(text: str) -> str:
    """Control characters and runs of white space as one space: names go into prompts, where a
    line break could start an instruction."""
    return " ".join(_CONTROL.sub(" ", text).split())


def _aliases(name: str, aliases: Iterable[str]) -> list[str]:
    """On one line, without duplicates (regardless of case); none may be the name."""
    result: list[str] = []
    keys = {name_key(name)}
    for alias in aliases:
        alias = require_name(one_line(alias), "alias")
        key = name_key(alias)
        if key == name_key(name):
            raise ValidationError(f"the alias '{alias}' is the contact's name")
        if key not in keys:
            keys.add(key)
            result.append(alias)
    if len(result) > MAX_ALIASES:
        raise ValidationError(f"a contact has at most {MAX_ALIASES} aliases")
    return result


_SOURCE_ORDER = {source: order for order, source in enumerate(IdentifierSource)}
_KIND_ORDER = {kind: order for order, kind in enumerate(IdentifierKind)}


def _identifiers(identifiers: Iterable[Identifier]) -> list[Identifier]:
    """Without duplicates (the first of a value wins), entered first, then learned, then
    rejected; at most `MAX_IDENTIFIERS` that count."""
    seen: set[tuple[IdentifierKind, str]] = set()
    result: list[Identifier] = []
    for item in identifiers:
        if item.key not in seen:
            seen.add(item.key)
            result.append(item)
    result.sort(key=lambda item: (_SOURCE_ORDER[item.source], _KIND_ORDER[item.kind], item.value))
    if sum(item.counts for item in result) > MAX_IDENTIFIERS:
        raise ValidationError(f"a contact has at most {MAX_IDENTIFIERS} identifiers")
    return result


MAX_TITLE_PATTERN = 500


def _title_pattern(pattern: str | None) -> str | None:
    """A template on one line (Liquid; the service checks its syntax and values)."""
    if pattern is None or not pattern.strip():
        return None
    pattern = one_line(pattern).strip()
    if len(pattern) > MAX_TITLE_PATTERN:
        raise ValidationError(f"a title pattern is at most {MAX_TITLE_PATTERN} characters")
    return pattern


def checked_description(description: str | None) -> str | None:
    """A description without surrounding space, None if empty; ValidationError if too long."""
    text = (description or "").strip()
    if len(text) > MAX_DESCRIPTION:
        raise ValidationError(f"description must not be longer than {MAX_DESCRIPTION} characters")
    return text or None
