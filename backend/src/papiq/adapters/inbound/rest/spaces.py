"""`/spaces`: the caller's spaces and those shared with them (every space for admins);
shares."""

from uuid import UUID

from fastapi import APIRouter

from papiq.adapters.inbound.rest.auth import PROTECTED, Authenticated
from papiq.adapters.inbound.rest.context import Context
from papiq.adapters.inbound.rest.problems import problem_responses
from papiq.adapters.inbound.rest.schemas.common import NameIn
from papiq.adapters.inbound.rest.schemas.spaces import ShareIn, SpaceCreate, SpaceOut
from papiq.core.domain.ids import SpaceId, UserId

router = APIRouter(prefix="/spaces", tags=["spaces"], dependencies=PROTECTED)

OWNER_ONLY = "The owner and admins."


@router.get(
    "",
    summary="List spaces",
    description=(
        "The spaces the caller owns or that are shared with them; every space for admins."
    ),
    response_model=list[SpaceOut],
    responses=problem_responses(401),
)
async def list_spaces(principal: Authenticated, context: Context) -> list[SpaceOut]:
    spaces = await context.spaces.list(principal.id)
    return [SpaceOut.of(d, principal.user) for d in sorted(spaces, key=lambda d: d.name.casefold())]


@router.post(
    "",
    status_code=201,
    summary="Create a space",
    description=(
        "Names are unique per owner regardless of case. The caller owns the space; admins may "
        "give `owner_id` to create one for another active user (others: 403, an unknown or "
        "deactivated user: 404)."
    ),
    response_model=SpaceOut,
    responses=problem_responses(401, 403, 404, 409, 422),
)
async def create_space(body: SpaceCreate, principal: Authenticated, context: Context) -> SpaceOut:
    owner = None if body.owner_id is None else UserId(body.owner_id)
    space = await context.spaces.create(principal.id, body.name, owner=owner)
    return SpaceOut.of(space, principal.user)


@router.get(
    "/{id}",
    summary="A space",
    response_model=SpaceOut,
    responses=problem_responses(401, 404, 422),
)
async def get_space(id: UUID, principal: Authenticated, context: Context) -> SpaceOut:
    return SpaceOut.of(await context.spaces.get(principal.id, SpaceId(id)), principal.user)


@router.patch(
    "/{id}",
    summary="Rename a space",
    description=OWNER_ONLY,
    response_model=SpaceOut,
    responses=problem_responses(401, 403, 404, 409, 422),
)
async def rename_space(
    id: UUID, body: NameIn, principal: Authenticated, context: Context
) -> SpaceOut:
    return SpaceOut.of(
        await context.spaces.rename(principal.id, SpaceId(id), body.name), principal.user
    )


@router.delete(
    "/{id}",
    status_code=204,
    summary="Delete a space",
    description=OWNER_ONLY + " Not the default space, and only while it is empty (409).",
    responses=problem_responses(401, 403, 404, 409, 422),
)
async def delete_space(id: UUID, principal: Authenticated, context: Context) -> None:
    await context.spaces.delete(principal.id, SpaceId(id))


@router.put(
    "/{id}/shares/{user_id}",
    summary="Share a space",
    description=(
        OWNER_ONLY + " Sets the user's access: `read` or `read_write`. The default space "
        "cannot be shared (422); an unknown or deactivated user is not found (404). Other "
        "users see documents in it once they are green."
    ),
    response_model=SpaceOut,
    responses=problem_responses(401, 403, 404, 422),
)
async def share_space(
    id: UUID, user_id: UUID, body: ShareIn, principal: Authenticated, context: Context
) -> SpaceOut:
    space = await context.spaces.share(principal.id, SpaceId(id), UserId(user_id), body.level)
    return SpaceOut.of(space, principal.user)


@router.delete(
    "/{id}/shares/{user_id}",
    summary="Stop sharing a space with a user",
    description=OWNER_ONLY,
    response_model=SpaceOut,
    responses=problem_responses(401, 403, 404, 422),
)
async def unshare_space(
    id: UUID, user_id: UUID, principal: Authenticated, context: Context
) -> SpaceOut:
    return SpaceOut.of(
        await context.spaces.unshare(principal.id, SpaceId(id), UserId(user_id)), principal.user
    )
