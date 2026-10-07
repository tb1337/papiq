"""Master data: contacts, document types, tags, attribute definitions. Everyone reads them;
admins change them. Deleting works only for what no document (or attribute) uses."""

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from fastapi import APIRouter

from papiq.adapters.inbound.rest.auth import PROTECTED, CurrentUser
from papiq.adapters.inbound.rest.context import ApiContext, Context
from papiq.adapters.inbound.rest.problems import problem_responses
from papiq.adapters.inbound.rest.schemas import (
    AttributeCreate,
    AttributeOut,
    MasterDataOut,
    NameIn,
)
from papiq.core.domain.ids import AttributeId, DocumentTypeId
from papiq.core.domain.master_data import MasterData

ADMINS_ONLY = "Admins only."

type Operation = Callable[..., Awaitable[Any]]


def _simple(path: str, kind: str, plural: str, name: str) -> APIRouter:
    """List, create, read, rename and delete one kind of named master data."""
    router = APIRouter(prefix=f"/{path}", tags=["master data"], dependencies=PROTECTED)

    def service(context: ApiContext, action: str) -> Operation:
        operation: Operation = getattr(context.master_data, f"{action}_{name}")
        return operation

    @router.get(
        "",
        name=f"list_{plural}",
        summary=f"List {kind}s",
        response_model=list[MasterDataOut],
        responses=problem_responses(401),
    )
    async def list_items(user: CurrentUser, context: Context) -> list[MasterDataOut]:
        operation: Operation = getattr(context.master_data, f"list_{plural}")
        items: list[MasterData] = await operation(user)
        return [MasterDataOut.of(item) for item in items]

    @router.post(
        "",
        name=f"create_{name}",
        status_code=201,
        summary=f"Create a {kind}",
        description=ADMINS_ONLY + " Names are unique regardless of case.",
        response_model=MasterDataOut,
        responses=problem_responses(401, 403, 409, 422),
    )
    async def create(body: NameIn, user: CurrentUser, context: Context) -> MasterDataOut:
        return MasterDataOut.of(await service(context, "create")(user, body.name))

    @router.get(
        "/{id}",
        name=f"get_{name}",
        summary=f"A {kind}",
        response_model=MasterDataOut,
        responses=problem_responses(401, 404, 422),
    )
    async def get(id: UUID, user: CurrentUser, context: Context) -> MasterDataOut:
        return MasterDataOut.of(await service(context, "get")(user, id))

    @router.patch(
        "/{id}",
        name=f"rename_{name}",
        summary=f"Rename a {kind}",
        description=ADMINS_ONLY,
        response_model=MasterDataOut,
        responses=problem_responses(401, 403, 404, 409, 422),
    )
    async def rename(id: UUID, body: NameIn, user: CurrentUser, context: Context) -> MasterDataOut:
        return MasterDataOut.of(await service(context, "rename")(user, id, body.name))

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


contacts = _simple("contacts", "contact", "contacts", "contact")
document_types = _simple("document-types", "document type", "document_types", "document_type")
tags = _simple("tags", "tag", "tags", "tag")
attributes = APIRouter(prefix="/attributes", tags=["master data"], dependencies=PROTECTED)


@attributes.get(
    "",
    summary="List attribute definitions",
    response_model=list[AttributeOut],
    responses=problem_responses(401),
)
async def list_attributes(user: CurrentUser, context: Context) -> list[AttributeOut]:
    return [AttributeOut.of(item) for item in await context.master_data.list_attributes(user)]


@attributes.post(
    "",
    status_code=201,
    summary="Create an attribute definition",
    description=(
        ADMINS_ONLY + " Global (`document_type_ids` null) or for some document types; `choice` "
        "needs `choices`."
    ),
    response_model=AttributeOut,
    responses=problem_responses(401, 403, 404, 409, 422),
)
async def create_attribute(
    body: AttributeCreate, user: CurrentUser, context: Context
) -> AttributeOut:
    item = await context.master_data.create_attribute(
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
    return AttributeOut.of(item)


@attributes.get(
    "/{id}",
    summary="An attribute definition",
    response_model=AttributeOut,
    responses=problem_responses(401, 404, 422),
)
async def get_attribute(id: UUID, user: CurrentUser, context: Context) -> AttributeOut:
    return AttributeOut.of(await context.master_data.get_attribute(user, AttributeId(id)))


@attributes.patch(
    "/{id}",
    summary="Rename an attribute definition",
    description=ADMINS_ONLY,
    response_model=AttributeOut,
    responses=problem_responses(401, 403, 404, 409, 422),
)
async def rename_attribute(
    id: UUID, body: NameIn, user: CurrentUser, context: Context
) -> AttributeOut:
    item = await context.master_data.rename_attribute(user, AttributeId(id), body.name)
    return AttributeOut.of(item)


@attributes.delete(
    "/{id}",
    status_code=204,
    summary="Delete an attribute definition",
    description=ADMINS_ONLY + " Only while no document has a value for it (409).",
    responses=problem_responses(401, 403, 404, 409, 422),
)
async def delete_attribute(id: UUID, user: CurrentUser, context: Context) -> None:
    await context.master_data.delete_attribute(user, AttributeId(id))


ROUTERS = [contacts, document_types, tags, attributes]
