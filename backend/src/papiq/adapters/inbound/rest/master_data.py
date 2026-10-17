"""Master data: contacts, document types, tags, field definitions. Everyone reads them;
admins change them. Deleting works only for what nothing uses."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel

from papiq.adapters.inbound.rest.auth import PROTECTED, CurrentUser
from papiq.adapters.inbound.rest.context import ApiContext, Context
from papiq.adapters.inbound.rest.paths import PREFIX
from papiq.adapters.inbound.rest.problems import problem_responses
from papiq.adapters.inbound.rest.schemas.master_data import (
    ContactCreate,
    ContactOut,
    ContactPatch,
    DocumentTypeCreate,
    DocumentTypeOut,
    DocumentTypePatch,
    FieldAssignmentsIn,
    FieldCreate,
    FieldOut,
    FieldPatch,
    IdentifierIn,
    MasterDataOut,
    TagCreate,
    TagOut,
    TagPatch,
)
from papiq.adapters.inbound.rest.upload import FILE_FIELD, read_upload
from papiq.core.domain.documents import UNSET, Unset
from papiq.core.domain.errors import ValidationError
from papiq.core.domain.identifiers import Identifier, IdentifierKind
from papiq.core.domain.ids import ContactId, DocumentTypeId, FieldId, TagId, UserId
from papiq.core.domain.master_data import MasterData, TagScope
from papiq.core.domain.tag_tree import TagTree
from papiq.core.ports import CountBy
from papiq.core.services.logos import LOGO_MAX_SIZE, LOGO_TYPE

ADMINS_ONLY = "Admins only."
COUNTS = (
    "Everyone reads them. With `document_counts`, each comes with how many documents within "
    "the caller's reach have it (a field: a value for it), counted in one query; admins count "
    "every document with `all_users` (others: 403)."
)
DocumentCounts = Annotated[
    bool, Query(description="Count the documents of each (`document_count`).")
]
AllUsers = Annotated[
    bool, Query(description="Count every user's documents instead of the caller's reach (admins).")
]

type Operation = Callable[..., Awaitable[Any]]


def _simple(
    path: str,
    kind: str,
    plural: str,
    name: str,
    out: type[MasterDataOut],
    create_in: type[BaseModel],
    patch_in: type[BaseModel],
    counted: CountBy,
    *,
    change: str = "rename",
    arguments: Callable[[dict[str, Any]], dict[str, Any]] = lambda given: given,
) -> APIRouter:
    """List, create, read, change (`change`: the service's verb) and delete one kind of named
    master data. The bodies' fields are the service's keyword arguments (as `arguments` turns
    them); a patch passes only the fields given."""
    router = APIRouter(prefix=f"/{path}", tags=["master data"], dependencies=PROTECTED)

    def service(context: ApiContext, action: str) -> Operation:
        operation: Operation = getattr(context.master_data, f"{action}_{name}")
        return operation

    @router.get(
        "",
        name=f"list_{plural}",
        summary=f"List {kind}s",
        description=COUNTS,
        response_model=list[out],  # type: ignore[valid-type]
        responses=problem_responses(401, 403, 422),
    )
    async def list_items(
        user: CurrentUser,
        context: Context,
        document_counts: DocumentCounts = False,
        all_users: AllUsers = False,
    ) -> list[MasterDataOut]:
        operation: Operation = getattr(context.master_data, f"list_{plural}")
        items: list[MasterData] = await operation(user)
        found = [out.of(item) for item in items]
        if not document_counts:
            return found
        counts = await context.documents.document_counts(user, counted, all_users=all_users)
        return [
            item.model_copy(update={"document_count": counts.get(item.id, 0)}) for item in found
        ]

    @router.post(
        "",
        name=f"create_{name}",
        status_code=201,
        summary=f"Create a {kind}",
        description=ADMINS_ONLY
        + " Names are unique regardless of case. A group in `group_ids` (a topic in "
        "`topic_ids`) that does not exist is `404`, one of another scope `422`.",
        response_model=out,
        responses=problem_responses(401, 403, 404, 409, 422),
    )
    async def create(
        body: create_in,  # type: ignore[valid-type]
        user: CurrentUser,
        context: Context,
    ) -> MasterDataOut:
        given = arguments(cast(BaseModel, body).model_dump())
        return out.of(await service(context, "create")(user, **given))

    @router.get(
        "/{id}",
        name=f"get_{name}",
        summary=f"A {kind}",
        response_model=out,
        responses=problem_responses(401, 404, 422),
    )
    async def get(id: UUID, user: CurrentUser, context: Context) -> MasterDataOut:
        return out.of(await service(context, "get")(user, id))

    @router.patch(
        "/{id}",
        name=f"{change}_{name}",
        summary=f"{change.capitalize()} a {kind}",
        description=ADMINS_ONLY
        + " A group in `group_ids` (a topic in `topic_ids`) that does not exist is `404`, one "
        "of another scope `422`.",
        response_model=out,
        responses=problem_responses(401, 403, 404, 409, 422),
    )
    async def patch(
        id: UUID,
        body: patch_in,  # type: ignore[valid-type]
        user: CurrentUser,
        context: Context,
    ) -> MasterDataOut:
        model = cast(BaseModel, body)
        given = arguments(model.model_dump(include=model.model_fields_set))
        return out.of(await service(context, change)(user, id, **given))

    @router.delete(
        "/{id}",
        name=f"delete_{name}",
        status_code=204,
        summary=f"Delete a {kind}",
        description=ADMINS_ONLY + " Only while no document uses it (409).",
        responses=problem_responses(401, 403, 404, 409, 422),
    )
    async def delete(id: UUID, user: CurrentUser, context: Context) -> None:
        await service(context, "delete")(user, id)

    return router


def _contact_arguments(given: dict[str, Any]) -> dict[str, Any]:
    """Identifiers as written, normalised (ValidationError, 422, if one is none), with their
    topic; a rejected one has none."""
    for key in ("identifiers", "rejected_identifiers"):
        if given.get(key) is not None:
            given[key] = [
                Identifier.parse(
                    IdentifierKind(item["kind"]),
                    item["value"],
                    topic_id=None
                    if key == "rejected_identifiers" or item.get("topic_id") is None
                    else TagId(item["topic_id"]),
                )
                for item in given[key]
            ]
    return given


contacts = _simple(
    "contacts", "contact", "contacts", "contact", ContactOut, ContactCreate, ContactPatch,
    CountBy.CONTACT, change="change", arguments=_contact_arguments,
)  # fmt: skip


@contacts.post(
    "/{id}/identifiers",
    summary="Attach an identifier to a contact",
    description=(
        ADMINS_ONLY + " Enters one identifier by hand (as found on a document, see `GET "
        "/documents/{id}/facts`), with a topic the contact enables (422 otherwise) or none; the "
        "contact's other identifiers stay as they are. One the contact learned or rejected is "
        "then entered by hand; one another contact learned or rejected moves here; 409 if "
        "another contact has it entered by hand. Documents use it from their next processing "
        "on."
    ),
    response_model=ContactOut,
    responses=problem_responses(401, 403, 404, 409, 422),
)
async def attach_identifier(
    id: UUID, body: IdentifierIn, user: CurrentUser, context: Context
) -> ContactOut:
    topic = None if body.topic_id is None else TagId(body.topic_id)
    identifier = Identifier.parse(body.kind, body.value, topic_id=topic)
    return ContactOut.of(await context.master_data.add_identifier(user, ContactId(id), identifier))


LOGO_PATH = f"{PREFIX}/contacts/{{id}}/logo"
_LOGO_BODY = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": [FILE_FIELD],
                    "properties": {
                        FILE_FIELD: {
                            "type": "string",
                            "format": "binary",
                            "description": "PNG, JPEG or WebP, recognised by content; at most "
                            f"{LOGO_MAX_SIZE} bytes.",
                        }
                    },
                }
            }
        },
    }
}


@contacts.put(
    "/{id}/logo",
    summary="Upload a contact's logo",
    description=(
        ADMINS_ONLY + " One part `file`: a PNG, JPEG or WebP image of at most 1 MiB (413 "
        "otherwise); another kind, SVG among them, is 415, an image that cannot be read 422. "
        "Papiq keeps it scaled to fit 128 px as WebP, in place of any other logo; a fetched "
        "one never replaces it."
    ),
    response_model=ContactOut,
    responses=problem_responses(400, 401, 403, 404, 413, 415, 422),
    openapi_extra=_LOGO_BODY,
)
async def upload_logo(
    id: UUID, request: Request, user: CurrentUser, context: Context
) -> ContactOut:
    received = await read_upload(request, max_size=LOGO_MAX_SIZE)
    try:
        data = await asyncio.to_thread(received.file.path.read_bytes)
    finally:
        await asyncio.shield(asyncio.to_thread(received.file.path.unlink, missing_ok=True))
    return ContactOut.of(await context.logos.upload(user, ContactId(id), data))


@contacts.delete(
    "/{id}/logo",
    status_code=204,
    summary="Remove a contact's logo",
    description=ADMINS_ONLY + " The contact shows its initials again.",
    responses=problem_responses(401, 403, 404, 422),
)
async def remove_logo(id: UUID, user: CurrentUser, context: Context) -> None:
    await context.logos.remove(user, ContactId(id))


@contacts.post(
    "/{id}/logo/fetch",
    status_code=202,
    summary="Fetch a contact's logo from its website",
    description=(
        ADMINS_ONLY + " Only with `PAPIQ_LOGO_FETCH` (503 otherwise; `GET /pipeline` tells, "
        "`logo_fetch`). The worker asks the website at each of the contact's domains in turn "
        "for its icon (HTTPS to public addresses only), also one whose fetch failed before, "
        "and stores the first that fits in place of a fetched logo; `ContactOut.logo` or "
        "`logo_failure` tell the outcome. 409 while the contact has an uploaded logo (it is "
        "never replaced), 422 without a domain among its identifiers."
    ),
    response_class=Response,
    responses={202: {"description": "Queued"}, **problem_responses(401, 403, 404, 409, 422, 503)},
)
async def fetch_logo(id: UUID, user: CurrentUser, context: Context) -> Response:
    await context.logos.request_fetch(user, ContactId(id))
    return Response(status_code=202)


@contacts.get(
    "/{id}/logo",
    summary="A contact's logo",
    description=(
        "Everyone reads it (master data is global): WebP, up to 128 px. Unlike the API's "
        "other answers it may be cached (`Cache-Control: private, max-age=86400`): its URL "
        "in `ContactOut.logo.url` carries its version. 404 without a logo."
    ),
    response_class=Response,
    responses={
        200: {"content": {LOGO_TYPE: {"schema": {"type": "string", "format": "binary"}}}},
        **problem_responses(401, 404, 422),
    },
)
async def get_logo(id: UUID, user: CurrentUser, context: Context) -> Response:
    stored = await context.logos.logo(user, ContactId(id))
    return Response(
        stored.data,
        media_type=LOGO_TYPE,
        headers={"Cache-Control": "private, max-age=86400"},
    )


document_types = _simple(
    "document-types",
    "document type",
    "document_types",
    "document_type",
    DocumentTypeOut,
    DocumentTypeCreate,
    DocumentTypePatch,
    CountBy.DOCUMENT_TYPE,
    change="change",
)
tags = APIRouter(prefix="/tags", tags=["master data"], dependencies=PROTECTED)


async def _tree(context: ApiContext, user: UserId) -> TagTree:
    return TagTree.of(await context.master_data.list_tags(user))


@tags.get(
    "",
    summary="List tags",
    description=(
        "Tags of every scope, or of `scope`: document tags, contact groups, type groups, "
        "topics. In the order of their trees: each after its parent, siblings by name (scopes "
        "in this order). Everyone reads them. With `document_counts`, each comes with how many "
        "documents within the caller's reach it stands for (`TagOut.document_count`); admins "
        "count every document with `all_users` (others: 403). A contact group or topic comes "
        "with its contacts (`contact_count`), a type group with its document types "
        "(`document_type_count`), whoever asks."
    ),
    response_model=list[TagOut],
    responses=problem_responses(401, 403, 422),
)
async def list_tags(
    user: CurrentUser,
    context: Context,
    scope: TagScope | None = None,
    document_counts: DocumentCounts = False,
    all_users: AllUsers = False,
) -> list[TagOut]:
    tree = await _tree(context, user)
    scopes = list(TagScope) if scope is None else [scope]
    found = [TagOut.of(tag, tree) for wanted in scopes for tag in tree.of_scope(wanted)]
    if not document_counts:
        return found
    counts = await context.documents.document_counts(user, CountBy.TAG, all_users=all_users)
    members = await context.master_data.tag_members(user)

    def counted(item: TagOut) -> TagOut:
        update: dict[str, int] = {"document_count": counts.get(item.id, 0)}
        if item.scope in (TagScope.CONTACT, TagScope.TOPIC):
            update["contact_count"] = members.contacts.get(TagId(item.id), 0)
        elif item.scope is TagScope.DOCUMENT_TYPE:
            update["document_type_count"] = members.document_types.get(TagId(item.id), 0)
        return item.model_copy(update=update)

    return [counted(item) for item in found]


@tags.post(
    "",
    status_code=201,
    summary="Create a tag",
    description=ADMINS_ONLY + " Names are unique per scope, regardless of case.",
    response_model=TagOut,
    responses=problem_responses(401, 403, 404, 409, 422),
)
async def create_tag(body: TagCreate, user: CurrentUser, context: Context) -> TagOut:
    tag = await context.master_data.create_tag(
        user,
        body.name,
        scope=body.scope,
        parent_id=None if body.parent_id is None else TagId(body.parent_id),
        assignment=body.assignment,
        shown=body.shown,
        icon=body.icon,
        color=body.color,
    )
    return TagOut.of(tag, await _tree(context, user))


@tags.get(
    "/{id}",
    summary="A tag",
    response_model=TagOut,
    responses=problem_responses(401, 404, 422),
)
async def get_tag(id: UUID, user: CurrentUser, context: Context) -> TagOut:
    tag = await context.master_data.get_tag(user, TagId(id))
    return TagOut.of(tag, await _tree(context, user))


@tags.patch(
    "/{id}",
    summary="Change a tag",
    description=(
        ADMINS_ONLY + " Name, parent (of the same scope, not below the tag itself; at most "
        "five levels), assignment, whether documents show it, icon and colour. The scope "
        "stays. A new name or parent brings the search index of the documents concerned up to "
        "date. After a new parent, a job leaves documents (for a group: contacts or document "
        "types) that carry the tag and one now above it only the one below; a topic needs "
        "none."
    ),
    response_model=TagOut,
    responses=problem_responses(401, 403, 404, 409, 422),
)
async def change_tag(id: UUID, body: TagPatch, user: CurrentUser, context: Context) -> TagOut:
    parent: TagId | Unset | None = UNSET
    if "parent_id" in body.model_fields_set:
        parent = None if body.parent_id is None else TagId(body.parent_id)
    given = body.model_fields_set
    tag = await context.master_data.change_tag(
        user,
        TagId(id),
        name=body.name,
        parent_id=parent,
        assignment=body.assignment,
        shown=body.shown,
        icon=body.icon if "icon" in given else UNSET,
        color=body.color if "color" in given else UNSET,
    )
    return TagOut.of(tag, await _tree(context, user))


@tags.delete(
    "/{id}",
    status_code=204,
    summary="Delete a tag",
    description=ADMINS_ONLY
    + " Only a tag without tags below it that nothing uses: no document carries it (as a "
    "tag or its topic), no contact or document type is in it, no contact enables it as a "
    "topic, no rule refers to it (409).",
    responses=problem_responses(401, 403, 404, 409, 422),
)
async def delete_tag(id: UUID, user: CurrentUser, context: Context) -> None:
    await context.master_data.delete_tag(user, TagId(id))


@document_types.put(
    "/{id}/fields",
    summary="Set the fields of a document type",
    description=(
        ADMINS_ONLY + " Exactly these fields belong to the type besides the global ones, each "
        "required or optional; in one transaction. A field taken off is no longer for the "
        "type: 409 while documents of the type have a value for it or the type's title "
        "pattern uses it; 422 for a field of no other type, or a global one. Returns the "
        "fields of the type, global ones aside."
    ),
    response_model=list[FieldOut],
    responses=problem_responses(401, 403, 404, 409, 422),
)
async def assign_fields(
    id: UUID, body: FieldAssignmentsIn, user: CurrentUser, context: Context
) -> list[FieldOut]:
    type_id = DocumentTypeId(id)
    if len({item.field_id for item in body.fields}) != len(body.fields):
        raise ValidationError("each field once")
    await context.master_data.assign_fields(
        user, type_id, {FieldId(item.field_id): item.required for item in body.fields}
    )
    return [
        FieldOut.of(item)
        for item in await context.master_data.list_fields(user)
        if item.document_type_ids is not None and type_id in item.document_type_ids
    ]


fields = APIRouter(prefix="/fields", tags=["master data"], dependencies=PROTECTED)


@fields.get(
    "",
    summary="List field definitions",
    description=COUNTS,
    response_model=list[FieldOut],
    responses=problem_responses(401, 403, 422),
)
async def list_fields(
    user: CurrentUser,
    context: Context,
    document_counts: DocumentCounts = False,
    all_users: AllUsers = False,
) -> list[FieldOut]:
    found = [FieldOut.of(item) for item in await context.master_data.list_fields(user)]
    if not document_counts:
        return found
    counts = await context.documents.document_counts(user, CountBy.FIELD, all_users=all_users)
    return [item.model_copy(update={"document_count": counts.get(item.id, 0)}) for item in found]


@fields.post(
    "",
    status_code=201,
    summary="Create a field definition",
    description=(
        ADMINS_ONLY + " Global (`document_type_ids` null) or for some document types; `choice` "
        "needs `choices`."
    ),
    response_model=FieldOut,
    responses=problem_responses(401, 403, 404, 409, 422),
)
async def create_field(body: FieldCreate, user: CurrentUser, context: Context) -> FieldOut:
    item = await context.master_data.create_field(
        user,
        body.name,
        body.data_type,
        document_type_ids=(
            None
            if body.document_type_ids is None
            else [DocumentTypeId(id) for id in body.document_type_ids]
        ),
        choices=body.choices,
        required=body.required,
        required_document_type_ids=[DocumentTypeId(id) for id in body.required_document_type_ids],
        description=body.description,
        assignment=body.assignment,
    )
    return FieldOut.of(item)


@fields.get(
    "/{id}",
    summary="A field definition",
    response_model=FieldOut,
    responses=problem_responses(401, 404, 422),
)
async def get_field(id: UUID, user: CurrentUser, context: Context) -> FieldOut:
    return FieldOut.of(await context.master_data.get_field(user, FieldId(id)))


@fields.patch(
    "/{id}",
    summary="Change a field definition",
    description=(
        ADMINS_ONLY + " Name, description, choices and scope; the data type stays. Values "
        "documents use are "
        "never changed: removing a used choice or narrowing the scope past documents with "
        "values is a conflict (409)."
    ),
    response_model=FieldOut,
    responses=problem_responses(401, 403, 404, 409, 422),
)
async def change_field(id: UUID, body: FieldPatch, user: CurrentUser, context: Context) -> FieldOut:
    scope: list[DocumentTypeId] | Unset | None = UNSET
    if "document_type_ids" in body.model_fields_set:
        scope = (
            None
            if body.document_type_ids is None
            else [DocumentTypeId(type_id) for type_id in body.document_type_ids]
        )
    required_types = body.required_document_type_ids
    item = await context.master_data.change_field(
        user,
        FieldId(id),
        name=body.name,
        choices=body.choices,
        document_type_ids=scope,
        required=body.required,
        required_document_type_ids=(
            None if required_types is None else [DocumentTypeId(id) for id in required_types]
        ),
        description=body.description if "description" in body.model_fields_set else UNSET,
        assignment=body.assignment,
    )
    return FieldOut.of(item)


@fields.delete(
    "/{id}",
    status_code=204,
    summary="Delete a field definition",
    description=ADMINS_ONLY + " Only while no document has a value for it (409).",
    responses=problem_responses(401, 403, 404, 409, 422),
)
async def delete_field(id: UUID, user: CurrentUser, context: Context) -> None:
    await context.master_data.delete_field(user, FieldId(id))


ROUTERS = [contacts, document_types, tags, fields]
