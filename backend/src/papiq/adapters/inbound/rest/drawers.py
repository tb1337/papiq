"""`/drawers`: the caller's drawers and those shared with them; shares."""

from uuid import UUID

from fastapi import APIRouter

from papiq.adapters.inbound.rest.auth import PROTECTED, CurrentUser
from papiq.adapters.inbound.rest.context import Context
from papiq.adapters.inbound.rest.problems import problem_responses
from papiq.adapters.inbound.rest.schemas import DrawerOut, NameIn, ShareIn
from papiq.core.domain.ids import DrawerId, UserId

router = APIRouter(prefix="/drawers", tags=["drawers"], dependencies=PROTECTED)

OWNER_ONLY = "Owner only."


@router.get(
    "",
    summary="List drawers",
    description="The drawers the caller owns or that are shared with them.",
    response_model=list[DrawerOut],
    responses=problem_responses(401),
)
async def list_drawers(user: CurrentUser, context: Context) -> list[DrawerOut]:
    drawers = await context.drawers.list(user)
    return [DrawerOut.of(d, user) for d in sorted(drawers, key=lambda d: d.name.casefold())]


@router.post(
    "",
    status_code=201,
    summary="Create a drawer",
    description="Names are unique per owner regardless of case.",
    response_model=DrawerOut,
    responses=problem_responses(401, 409, 422),
)
async def create_drawer(body: NameIn, user: CurrentUser, context: Context) -> DrawerOut:
    return DrawerOut.of(await context.drawers.create(user, body.name), user)


@router.get(
    "/{id}",
    summary="A drawer",
    response_model=DrawerOut,
    responses=problem_responses(401, 404, 422),
)
async def get_drawer(id: UUID, user: CurrentUser, context: Context) -> DrawerOut:
    return DrawerOut.of(await context.drawers.get(user, DrawerId(id)), user)


@router.patch(
    "/{id}",
    summary="Rename a drawer",
    description=OWNER_ONLY,
    response_model=DrawerOut,
    responses=problem_responses(401, 403, 404, 409, 422),
)
async def rename_drawer(id: UUID, body: NameIn, user: CurrentUser, context: Context) -> DrawerOut:
    return DrawerOut.of(await context.drawers.rename(user, DrawerId(id), body.name), user)


@router.delete(
    "/{id}",
    status_code=204,
    summary="Delete a drawer",
    description=OWNER_ONLY + " Not the default drawer, and only while it is empty (409).",
    responses=problem_responses(401, 403, 404, 409, 422),
)
async def delete_drawer(id: UUID, user: CurrentUser, context: Context) -> None:
    await context.drawers.delete(user, DrawerId(id))


@router.put(
    "/{id}/shares/{user_id}",
    summary="Share a drawer",
    description=(
        OWNER_ONLY + " Sets the user's access: `read` or `read_write`. The default drawer "
        "cannot be shared (422). Other users see documents in it once they are green."
    ),
    response_model=DrawerOut,
    responses=problem_responses(401, 403, 404, 422),
)
async def share_drawer(
    id: UUID, user_id: UUID, body: ShareIn, user: CurrentUser, context: Context
) -> DrawerOut:
    drawer = await context.drawers.share(user, DrawerId(id), UserId(user_id), body.level)
    return DrawerOut.of(drawer, user)


@router.delete(
    "/{id}/shares/{user_id}",
    summary="Stop sharing a drawer with a user",
    description=OWNER_ONLY,
    response_model=DrawerOut,
    responses=problem_responses(401, 403, 404, 422),
)
async def unshare_drawer(id: UUID, user_id: UUID, user: CurrentUser, context: Context) -> DrawerOut:
    return DrawerOut.of(await context.drawers.unshare(user, DrawerId(id), UserId(user_id)), user)
