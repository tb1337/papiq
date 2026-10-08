"""`/drawers`: the caller's drawers and those shared with them (every drawer for admins);
shares."""

from uuid import UUID

from fastapi import APIRouter

from papiq.adapters.inbound.rest.auth import PROTECTED, Authenticated
from papiq.adapters.inbound.rest.context import Context
from papiq.adapters.inbound.rest.problems import problem_responses
from papiq.adapters.inbound.rest.schemas import DrawerCreate, DrawerOut, NameIn, ShareIn
from papiq.core.domain.ids import DrawerId, UserId

router = APIRouter(prefix="/drawers", tags=["drawers"], dependencies=PROTECTED)

OWNER_ONLY = "The owner and admins."


@router.get(
    "",
    summary="List drawers",
    description=(
        "The drawers the caller owns or that are shared with them; every drawer for admins."
    ),
    response_model=list[DrawerOut],
    responses=problem_responses(401),
)
async def list_drawers(principal: Authenticated, context: Context) -> list[DrawerOut]:
    drawers = await context.drawers.list(principal.id)
    return [
        DrawerOut.of(d, principal.user) for d in sorted(drawers, key=lambda d: d.name.casefold())
    ]


@router.post(
    "",
    status_code=201,
    summary="Create a drawer",
    description=(
        "Names are unique per owner regardless of case. The caller owns the drawer; admins may "
        "give `owner_id` to create one for another active user (others: 403, an unknown or "
        "deactivated user: 404)."
    ),
    response_model=DrawerOut,
    responses=problem_responses(401, 403, 404, 409, 422),
)
async def create_drawer(
    body: DrawerCreate, principal: Authenticated, context: Context
) -> DrawerOut:
    owner = None if body.owner_id is None else UserId(body.owner_id)
    drawer = await context.drawers.create(principal.id, body.name, owner=owner)
    return DrawerOut.of(drawer, principal.user)


@router.get(
    "/{id}",
    summary="A drawer",
    response_model=DrawerOut,
    responses=problem_responses(401, 404, 422),
)
async def get_drawer(id: UUID, principal: Authenticated, context: Context) -> DrawerOut:
    return DrawerOut.of(await context.drawers.get(principal.id, DrawerId(id)), principal.user)


@router.patch(
    "/{id}",
    summary="Rename a drawer",
    description=OWNER_ONLY,
    response_model=DrawerOut,
    responses=problem_responses(401, 403, 404, 409, 422),
)
async def rename_drawer(
    id: UUID, body: NameIn, principal: Authenticated, context: Context
) -> DrawerOut:
    return DrawerOut.of(
        await context.drawers.rename(principal.id, DrawerId(id), body.name), principal.user
    )


@router.delete(
    "/{id}",
    status_code=204,
    summary="Delete a drawer",
    description=OWNER_ONLY + " Not the default drawer, and only while it is empty (409).",
    responses=problem_responses(401, 403, 404, 409, 422),
)
async def delete_drawer(id: UUID, principal: Authenticated, context: Context) -> None:
    await context.drawers.delete(principal.id, DrawerId(id))


@router.put(
    "/{id}/shares/{user_id}",
    summary="Share a drawer",
    description=(
        OWNER_ONLY + " Sets the user's access: `read` or `read_write`. The default drawer "
        "cannot be shared (422); an unknown or deactivated user is not found (404). Other "
        "users see documents in it once they are green."
    ),
    response_model=DrawerOut,
    responses=problem_responses(401, 403, 404, 422),
)
async def share_drawer(
    id: UUID, user_id: UUID, body: ShareIn, principal: Authenticated, context: Context
) -> DrawerOut:
    drawer = await context.drawers.share(principal.id, DrawerId(id), UserId(user_id), body.level)
    return DrawerOut.of(drawer, principal.user)


@router.delete(
    "/{id}/shares/{user_id}",
    summary="Stop sharing a drawer with a user",
    description=OWNER_ONLY,
    response_model=DrawerOut,
    responses=problem_responses(401, 403, 404, 422),
)
async def unshare_drawer(
    id: UUID, user_id: UUID, principal: Authenticated, context: Context
) -> DrawerOut:
    return DrawerOut.of(
        await context.drawers.unshare(principal.id, DrawerId(id), UserId(user_id)), principal.user
    )
