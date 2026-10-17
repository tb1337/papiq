"""Contacts, document types, tags and fields."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from papiq.adapters.inbound.rest.paths import logo_url
from papiq.adapters.inbound.rest.schemas.common import Name, NameIn
from papiq.core.domain.appearance import MAX_ICON, ContactLogo, LogoSource, PaletteColor
from papiq.core.domain.fields import FieldDefinition, FieldType
from papiq.core.domain.identifiers import MAX_IDENTIFIERS, IdentifierKind, IdentifierSource
from papiq.core.domain.master_data import (
    MAX_ALIASES,
    MAX_DESCRIPTION,
    MAX_GROUPS,
    MAX_TITLE_PATTERN,
    MAX_TOPICS,
    Assignment,
    Contact,
    DocumentType,
    MasterData,
    Tag,
    TagScope,
)
from papiq.core.domain.tag_tree import TagTree


class MasterDataOut(BaseModel):
    id: UUID
    name: str = Field(examples=["ACME Energy"])
    created_at: datetime
    document_count: int | None = Field(
        default=None,
        description=(
            "With `document_counts`: how many documents within the caller's reach (with "
            "`all_users`, every document) have it; for a field, a value for it."
        ),
    )

    @classmethod
    def of(cls, item: MasterData) -> "MasterDataOut":
        return cls(id=getattr(item, "id"), name=item.name, created_at=item.created_at)  # noqa: B009


Aliases = Annotated[
    list[Name],
    Field(
        max_length=MAX_ALIASES,
        description=(
            "Other names the contact is written as in documents. Unique across contacts, "
            "together with the names; papiq also learns them when a contact is chosen in the "
            "review."
        ),
        examples=[["ACME Energy Services GmbH"]],
    ),
]


class IdentifierIn(BaseModel):
    """Written as in documents; papiq stores it normalised (an IBAN without spaces, a domain
    without `www.`)."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"kind": "customer_number", "value": "2004-7718"}]},
    )

    kind: IdentifierKind
    value: str = Field(min_length=1, max_length=100, examples=["DE89 3704 0044 0532 0130 00"])
    topic_id: UUID | None = Field(
        default=None,
        description="One of the contact's topics it points to (a personnel number to work): "
        "found in a document, it is evidence for the contact and the topic. Not for a rejected "
        "one.",
    )


class IdentifierOut(BaseModel):
    kind: IdentifierKind
    value: str = Field(examples=["DE89370400440532013000"])
    source: IdentifierSource = Field(
        description="`manual`: entered by an admin; `learned`: found by training in "
        "confirmed documents of only this contact; `rejected`: learned, then rejected by an "
        "admin (does not count, and training does not learn it again)."
    )
    topic_id: UUID | None = Field(
        description="The topic of the contact it points to; a learned one points to a topic "
        "only if all the confirmed documents it stands in carry it."
    )


Identifiers = Annotated[
    list[IdentifierIn],
    Field(
        max_length=MAX_IDENTIFIERS,
        description=(
            "Values the contact's documents carry: IBAN, VAT number, customer (or contract) "
            "number, the personnel number at an employer (without leading zeros), the domain "
            "of its e-mail and web addresses. Unique across contacts; found "
            "in a document, they are evidence for the contact. The identifiers entered by "
            "hand: a learned one given here becomes one entered by hand."
        ),
    ),
]

Rejected = Annotated[
    list[IdentifierIn],
    Field(
        max_length=MAX_IDENTIFIERS,
        description="The rejected identifiers: a learned one given here no longer counts, and "
        "training does not learn it again; one left out may be learned again.",
    ),
]


Icon = Annotated[
    str,
    Field(
        max_length=MAX_ICON,
        pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$",
        description=(
            "The name of a Lucide icon (lucide.dev), as `heart-pulse`. Only the form is "
            "checked; clients show a stand-in for a name they do not know. Null removes it."
        ),
        examples=["receipt-euro"],
    ),
]
Color = Annotated[
    str,
    Field(
        pattern=rf"^({'|'.join(PaletteColor)}|#[0-9a-fA-F]{{6}})$",
        description=(
            "One of the twelve colours of the palette, which clients tune for light and dark "
            "themes, or an own colour as `#rrggbb` (stored in lower case; clients mix it with "
            "their background and text colours so the text stays readable). Null removes it."
        ),
        examples=["teal", "#b45309"],
    ),
]

Description = Annotated[
    str,
    Field(
        max_length=MAX_DESCRIPTION,
        description="What belongs to the type, for the language model; empty removes it.",
        examples=["Gehaltsabrechnung, Entgeltbescheinigung, Verdienstnachweis"],
    ),
]
FieldDescription = Annotated[
    str,
    Field(
        max_length=MAX_DESCRIPTION,
        description=(
            "What belongs in the field, for the language model (the field's name is often an "
            "internal one no document shows); empty removes it."
        ),
        examples=["Gesamtbrutto des Monats, nicht das Grundgehalt; steht als Gesamt-Brutto"],
    ),
]


TitlePattern = Annotated[
    str,
    Field(
        max_length=MAX_TITLE_PATTERN,
        description=(
            "The title of the type's documents: a template as a rule's title "
            "(`SetTitleAction.template`), with the fields of the type. A rule of the type "
            "carries it out (`RuleOut.managed_by_document_type_id`, read-only): it replaces "
            "the language model's title and gives way to another rule that sets the title. A "
            "document missing a value it writes keeps the model's title."
        ),
        examples=["Lohn {{ document_date | date: '%Y-%m' }} {{ field.la_brutto }}"],
    ),
]


def _groups(scope: str) -> object:
    return Field(
        max_length=MAX_GROUPS,
        description=(
            f"The {scope} groups it is in (tags of scope `{scope.replace(' ', '_')}`); its "
            "documents inherit them. A group set with one below it leaves the one below."
        ),
    )


ContactGroups = Annotated[list[UUID], _groups("contact")]
TypeGroups = Annotated[list[UUID], _groups("document type")]
ContactTopics = Annotated[
    list[UUID],
    Field(
        max_length=MAX_TOPICS,
        description=(
            "The topics it enables (tags of scope `topic`): its documents carry one of them at "
            "most, and its identifiers may point to one. Taking one away is a conflict (409) "
            "while a document of the contact carries it; its identifiers lose it."
        ),
    ),
]


class ContactCreate(NameIn):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"name": "ACME Energy", "aliases": ["ACME Strom"]}]},
    )

    aliases: Aliases = Field(default_factory=list)
    identifiers: Identifiers = Field(default_factory=list)
    group_ids: ContactGroups = Field(default_factory=list)
    topic_ids: ContactTopics = Field(default_factory=list)


class ContactPatch(BaseModel):
    """Fields left out stay; `aliases` replaces all aliases, `identifiers` those entered by
    hand and `rejected_identifiers` the rejected ones. A value another contact learned or
    rejected moves here; one another contact entered by hand is a conflict (409)."""

    model_config = ConfigDict(
        extra="forbid", json_schema_extra={"examples": [{"aliases": ["ACME Strom"]}]}
    )

    name: Name | None = None
    aliases: Aliases | None = None
    identifiers: Identifiers | None = None
    rejected_identifiers: Rejected | None = None
    group_ids: ContactGroups | None = None
    topic_ids: ContactTopics | None = None


class LogoOut(BaseModel):
    source: LogoSource = Field(
        description="`uploaded` by an admin (a fetched one never replaces it) or `fetched` "
        "from the contact's website."
    )
    domain: str | None = Field(
        description="Of a fetched one: the domain whose website it came from.",
        examples=["acme-energy.example"],
    )
    stored_at: datetime
    url: str = Field(
        description="Where to get it (`GET /contacts/{id}/logo`, WebP up to 128 px), with its "
        "version: a new logo has a new URL, so it may be cached.",
        examples=["/api/v1/contacts/01999d5e-1111-7c1e-b6a3-2f4d5e6f7a8b/logo?v=1792200000000000"],
    )

    @classmethod
    def of(cls, contact: Contact, logo: ContactLogo) -> "LogoOut":
        version = int(logo.stored_at.timestamp() * 1_000_000)
        return cls(
            source=logo.source,
            domain=logo.domain,
            stored_at=logo.stored_at,
            url=logo_url(contact.id, version),
        )


class LogoFailureOut(BaseModel):
    domains: list[str] = Field(
        description="The domains whose website had no logo that fits, sorted.",
        examples=[["acme-energy.example"]],
    )
    failed_at: datetime = Field(description="When the last of them failed.")


class ContactOut(MasterDataOut):
    aliases: list[str] = Field(examples=[["ACME Strom"]])
    identifiers: list[IdentifierOut] = Field(
        description="Entered, learned and rejected identifiers, in this order."
    )
    group_ids: list[UUID] = Field(description="The contact groups it is in.")
    topic_ids: list[UUID] = Field(description="The topics it enables.")
    logo: LogoOut | None = Field(description="Its logo; without one, clients show initials.")
    logo_failure: LogoFailureOut | None = Field(
        description="The domains whose website had no logo that fits; they are tried again "
        "only when an admin asks (`POST /contacts/{id}/logo/fetch`), another domain at once."
    )

    @classmethod
    def of(cls, item: MasterData) -> "ContactOut":
        assert isinstance(item, Contact)
        return cls(
            id=item.id,
            name=item.name,
            aliases=item.aliases,
            identifiers=[
                IdentifierOut(
                    kind=entry.kind,
                    value=entry.value,
                    source=entry.source,
                    topic_id=entry.topic_id,
                )
                for entry in item.identifiers
            ],
            group_ids=sorted(item.group_ids),
            topic_ids=sorted(item.topic_ids),
            logo=None if item.logo is None else LogoOut.of(item, item.logo),
            logo_failure=(
                None
                if item.logo_failure is None
                else LogoFailureOut(
                    domains=sorted(item.logo_failure.domains),
                    failed_at=item.logo_failure.failed_at,
                )
            ),
            created_at=item.created_at,
        )


class DocumentTypeCreate(NameIn):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [{"name": "Pay slip", "description": "Entgeltbescheinigung"}]
        },
    )

    description: Description | None = None
    title_pattern: TitlePattern | None = None
    group_ids: TypeGroups = Field(default_factory=list)
    icon: Icon | None = None


class DocumentTypePatch(BaseModel):
    """Fields left out stay; a `description` or `title_pattern` of null or empty removes
    it."""

    model_config = ConfigDict(
        extra="forbid", json_schema_extra={"examples": [{"description": "Entgeltbescheinigung"}]}
    )

    name: Name | None = None
    description: Description | None = None
    title_pattern: TitlePattern | None = None
    group_ids: TypeGroups | None = None
    icon: Icon | None = None


class DocumentTypeOut(MasterDataOut):
    description: str | None = Field(examples=["Entgeltbescheinigung"])
    title_pattern: str | None = Field(examples=["Lohn {document_date:YYYY-MM}"])
    group_ids: list[UUID] = Field(description="The document type groups it is in.")
    icon: str | None = Field(description="The name of a Lucide icon.", examples=["receipt"])

    @classmethod
    def of(cls, item: MasterData) -> "DocumentTypeOut":
        assert isinstance(item, DocumentType)
        return cls(
            id=item.id,
            name=item.name,
            description=item.description,
            title_pattern=item.title_pattern,
            group_ids=sorted(item.group_ids),
            icon=item.icon,
            created_at=item.created_at,
        )


TagAssignment = Annotated[
    Assignment,
    Field(
        description="`auto`: the classification proposes and sets it (document tags and "
        "topics); `manual`: only a person or a rule sets it (a status such as 'to pay'). Groups "
        "are always `manual`."
    ),
]
Shown = Annotated[
    bool,
    Field(
        description="Whether documents show it (the chips of lists and the document; filters, "
        "search, rules and templates see it either way). Default: on for document tags and "
        "topics, off for groups."
    ),
]


class TagCreate(NameIn):
    """Names are unique per scope. `parent_id`: a tag of the same scope; at most five levels.
    `assignment` and `shown` follow the scope unless given."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {"name": "Health insurance", "parent_id": "01999d5e-2222-7c1e-b6a3-2f4d5e6f7a8b"},
                {"name": "Insurer", "scope": "contact"},
            ]
        },
    )

    scope: TagScope = Field(
        default=TagScope.DOCUMENT,
        description="`document`: a tag of documents; `contact`: a contact group; "
        "`document_type`: a type group; `topic`: a topic, enabled at contacts, one per "
        "document at most. Fixed once the tag exists.",
    )
    parent_id: UUID | None = None
    assignment: TagAssignment | None = None
    shown: Shown | None = None
    icon: Icon | None = None
    color: Color | None = None


class TagPatch(BaseModel):
    """Fields left out stay; a `parent_id` of null makes it a top-level tag. The scope stays."""

    model_config = ConfigDict(
        extra="forbid", json_schema_extra={"examples": [{"parent_id": None, "shown": False}]}
    )

    name: Name | None = None
    parent_id: UUID | None = None
    assignment: TagAssignment | None = None
    shown: Shown | None = None
    icon: Icon | None = None
    color: Color | None = None


class TagOut(MasterDataOut):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "id": "01999d5e-2223-7c1e-b6a3-2f4d5e6f7a8b",
                    "name": "Health insurance",
                    "scope": "document",
                    "parent_id": "01999d5e-2222-7c1e-b6a3-2f4d5e6f7a8b",
                    "path": ["Insurance", "Health insurance"],
                    "assignment": "auto",
                    "shown": True,
                    "icon": "heart-pulse",
                    "color": "pink",
                    "created_at": "2026-01-10T09:00:00Z",
                }
            ]
        }
    )

    scope: TagScope
    parent_id: UUID | None
    path: list[str] = Field(
        description="The names from the top-level tag down to its own; documents show the "
        "last, its path tells where it stands."
    )
    assignment: TagAssignment
    shown: Shown
    icon: str | None = Field(description="The name of a Lucide icon.")
    color: str | None = Field(description="A colour of the palette or `#rrggbb`.")
    document_count: int | None = Field(
        default=None,
        description=(
            "With `document_counts`: how many documents within the caller's reach (with "
            "`all_users`, every document) it stands for: those with it or a tag below it, each "
            "once; for a group, those whose contact (type) is in it or a group below it; for a "
            "topic, those with it or a topic below it."
        ),
    )
    contact_count: int | None = Field(
        default=None,
        description=(
            "With `document_counts`, for a contact group or a topic (else null): how many "
            "contacts are in it or a group below it (enable it or a topic below it), each once."
        ),
    )
    document_type_count: int | None = Field(
        default=None,
        description=(
            "With `document_counts`, for a type group (else null): how many document types are "
            "in it or a group below it, each once."
        ),
    )

    @classmethod
    def of(cls, item: MasterData, tree: TagTree | None = None) -> "TagOut":
        assert isinstance(item, Tag)
        path = [item.name] if tree is None or item.id not in tree else tree.path(item.id)
        return cls(
            id=item.id,
            name=item.name,
            scope=item.scope,
            parent_id=item.parent_id,
            path=path,
            assignment=item.assignment,
            shown=item.shown,
            icon=item.icon,
            color=item.color,
            created_at=item.created_at,
        )


Required = Annotated[
    bool,
    Field(
        description="A global field: whether every document needs a value (read by the model, "
        "or entered by hand for a field set by hand)."
    ),
]
RequiredTypes = Annotated[
    list[UUID],
    Field(
        description=(
            "A typed field: the document types (of its own) it is required for; for the others "
            "it is optional. Extraction must find a required field; an optional one may stay "
            "empty, but a value it gives must be shown by the text."
        )
    ),
]


class FieldCreate(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "name": "LA_Brutto",
                    "data_type": "amount",
                    "document_type_ids": ["01999d5e-3333-7c1e-b6a3-2f4d5e6f7a8b"],
                    "description": "Gesamtbrutto des Monats, nicht das Grundgehalt",
                }
            ]
        },
    )

    name: Name
    data_type: FieldType
    document_type_ids: list[UUID] | None = Field(
        default=None, description="null: global; otherwise only for these document types."
    )
    choices: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        default_factory=list, max_length=200, description="For `choice` only."
    )
    required: Required = False
    required_document_type_ids: RequiredTypes = Field(default_factory=list)
    description: FieldDescription | None = None
    assignment: Assignment = Field(
        default=Assignment.AUTO,
        description="`auto`: the language model reads it; `manual`: set by hand only, never "
        "asked of the model (a required one keeps the document open until a person sets it).",
    )


class FieldPatch(BaseModel):
    """Fields left out stay; a `description` of null or empty removes it. The data type
    cannot change."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [{"name": "Payment method", "choices": ["Card", "Transfer", "Cash"]}]
        },
    )

    name: Name | None = None
    choices: list[Annotated[str, Field(min_length=1, max_length=200)]] | None = Field(
        default=None,
        max_length=200,
        description="All choices; removing one that documents use is a conflict (409).",
    )
    document_type_ids: list[UUID] | None = Field(
        default=None,
        description=(
            "null: global. Narrowing is a conflict (409) while documents outside the new scope "
            "have values; the types it is required for narrow with it."
        ),
    )
    required: Required | None = None
    required_document_type_ids: RequiredTypes | None = None
    description: FieldDescription | None = None
    assignment: Assignment | None = Field(
        default=None,
        description="`auto`: the language model reads it; `manual`: set by hand only, never "
        "asked of the model (a required one keeps the document open until a person sets it).",
    )


class FieldOut(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "id": "01999d5e-4444-7c1e-b6a3-2f4d5e6f7a8b",
                    "name": "Amount",
                    "key": "amount",
                    "data_type": "amount",
                    "document_type_ids": None,
                    "choices": [],
                    "required": False,
                    "required_document_type_ids": [],
                    "description": "Gesamtbrutto des Monats, nicht das Grundgehalt",
                    "assignment": "auto",
                    "created_at": "2026-01-10T09:00:00Z",
                }
            ]
        }
    )

    id: UUID
    name: str
    key: str = Field(
        description="The field in templates of rules and title patterns: `field.<key>`, from "
        "the name (lower case, umlauts as ae/oe/ue, anything else `_`)"
    )
    data_type: FieldType
    document_type_ids: list[UUID] | None
    choices: list[str]
    required: Required
    required_document_type_ids: RequiredTypes
    description: str | None
    assignment: Assignment = Field(
        description="`auto`: the language model reads it; `manual`: set by hand only, never "
        "asked of the model (a required one keeps the document open until a person sets it).",
    )
    created_at: datetime
    document_count: int | None = Field(
        default=None,
        description=(
            "With `document_counts`: how many documents within the caller's reach (with "
            "`all_users`, every document) have it; for a field, a value for it."
        ),
    )

    @classmethod
    def of(cls, item: FieldDefinition) -> "FieldOut":
        return cls(
            id=item.id,
            name=item.name,
            key=item.key,
            data_type=item.data_type,
            document_type_ids=(
                None if item.document_type_ids is None else sorted(item.document_type_ids)
            ),
            choices=list(item.choices),
            required=item.required,
            required_document_type_ids=sorted(item.required_types),
            description=item.description,
            assignment=item.assignment,
            created_at=item.created_at,
        )


class FieldAssignmentIn(BaseModel):
    """A field of a document type, from the type."""

    model_config = ConfigDict(extra="forbid")

    field_id: UUID
    required: bool = False


class FieldAssignmentsIn(BaseModel):
    """The fields of a document type, global ones aside."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "fields": [
                        {"field_id": "01999d5e-4444-7c1e-b6a3-2f4d5e6f7a8b", "required": True},
                        {"field_id": "01999d5e-5555-7c1e-b6a3-2f4d5e6f7a8b", "required": False},
                    ]
                }
            ]
        },
    )

    fields: list[FieldAssignmentIn] = Field(max_length=500)
