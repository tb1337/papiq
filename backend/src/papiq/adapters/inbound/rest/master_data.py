"""Master data: contacts, document types, tags, field definitions. Everyone reads them;
admins change them. Deleting works only for what no document (or field) uses."""

from collections.abc import Awaitable, Callable
from typing import Any, cast
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel

from papiq.adapters.inbound.rest.auth import PROTECTED, CurrentUser
from papiq.adapters.inbound.rest.context import ApiContext, Context
from papiq.adapters.inbound.rest.problems import problem_responses
from papiq.adapters.inbound.rest.schemas import (
    ContactCreate,
    ContactOut,
    ContactPatch,
    DocumentTypeCreate,
    DocumentTypeOut,
    DocumentTypePatch,
    FieldCreate,
    FieldOut,
    FieldPatch,
    MasterDataOut,
    NameIn,
)
from papiq.core.domain.documents import UNSET, Unset
from papiq.core.domain.ids import DocumentTypeId, FieldId
from papiq.core.domain.master_data import MasterData

ADMINS_ONLY = "Admins only."

type Operation = Callable[..., Awaitable[Any]]


def _simple(
    path: str,
    kind: str,
    plural: str,
    name: str,
    out: type[MasterDataOut],
    create_in: type[BaseModel],
    patch_in: type[BaseModel],
    *,
    change: str = "rename",
) -> APIRouter:
    """List, create, read, change (`change`: the service's verb) and delete one kind of named
    master data. The bodies' fields are the service's keyword arguments; a patch passes only
    the fields given."""
    router = APIRouter(prefix=f"/{path}", tags=["master data"], dependencies=PROTECTED)

    def service(context: ApiContext, action: str) -> Operation:
        operation: Operation = getattr(context.master_data, f"{action}_{name}")
        return operation

    @router.get(
        "",
        name=f"list_{plural}",
        summary=f"List {kind}s",
        response_model=list[out],  # type: ignore[valid-type]
        responses=problem_responses(401),
    )
    async def list_items(user: CurrentUser, context: Context) -> list[MasterDataOut]:
        operation: Operation = getattr(context.master_data, f"list_{plural}")
        items: list[MasterData] = await operation(user)
        return [out.of(item) for item in items]

    @router.post(
        "",
        name=f"create_{name}",
        status_code=201,
        summary=f"Create a {kind}",
        description=ADMINS_ONLY + " Names are unique regardless of case.",
        response_model=out,
        responses=problem_responses(401, 403, 409, 422),
    )
    async def create(
        body: create_in,  # type: ignore[valid-type]
        user: CurrentUser,
        context: Context,
    ) -> MasterDataOut:
        return out.of(await service(context, "create")(user, **cast(BaseModel, body).model_dump()))

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
        description=ADMINS_ONLY,
        response_model=out,
        responses=problem_responses(401, 403, 404, 409, 422),
    )
    async def patch(
        id: UUID,
        body: patch_in,  # type: ignore[valid-type]
        user: CurrentUser,
        context: Context,
    ) -> MasterDataOut:
        given = {field: getattr(body, field) for field in cast(BaseModel, body).model_fields_set}
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


contacts = _simple(
    "contacts", "contact", "contacts", "contact", ContactOut, ContactCreate, ContactPatch,
    change="change",
)  # fmt: skip
document_types = _simple(
    "document-types",
    "document type",
    "document_types",
    "document_type",
    DocumentTypeOut,
    DocumentTypeCreate,
    DocumentTypePatch,
    change="change",
)
tags = _simple("tags", "tag", "tags", "tag", MasterDataOut, NameIn, NameIn)
fields = APIRouter(prefix="/fields", tags=["master data"], dependencies=PROTECTED)


@fields.get(
    "",
    summary="List field definitions",
    response_model=list[FieldOut],
    responses=problem_responses(401),
)
async def list_fields(user: CurrentUser, context: Context) -> list[FieldOut]:
    return [FieldOut.of(item) for item in await context.master_data.list_fields(user)]


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
        ADMINS_ONLY + " Name, choices and scope; the data type stays. Values documents use are "
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
    item = await context.master_data.change_field(
        user, FieldId(id), name=body.name, choices=body.choices, document_type_ids=scope
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
