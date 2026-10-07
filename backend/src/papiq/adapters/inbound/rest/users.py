"""`/users`: accounts. Admins manage them; everyone reads the active users' names, to share
drawers with them."""

from uuid import UUID

from fastapi import APIRouter

from papiq.adapters.inbound.rest.auth import PROTECTED, Authenticated, CurrentUser
from papiq.adapters.inbound.rest.context import Context
from papiq.adapters.inbound.rest.problems import problem_responses
from papiq.adapters.inbound.rest.schemas import (
    AccountOut,
    LinkOut,
    PasswordReset,
    Removed,
    UserCreate,
    UserOut,
    UserPatch,
)
from papiq.core.domain.ids import UserId
from papiq.core.services.users import Account

router = APIRouter(prefix="/users", tags=["users"], dependencies=PROTECTED)

ADMINS_ONLY = "Admins only."


def _account(account: Account) -> AccountOut:
    return AccountOut(
        **UserOut.of(account.user).model_dump(),
        has_password=account.has_password,
        totp_enabled=account.totp_enabled,
        linked_accounts=[LinkOut.of(link) for link in account.external_identities],
    )


@router.get(
    "",
    summary="List users",
    description=(
        "Admins see all users with role and state; others see the active users' id and name."
    ),
    response_model=list[UserOut],
    response_model_exclude_none=True,
    responses=problem_responses(401),
)
async def list_users(principal: Authenticated, context: Context) -> list[UserOut]:
    full = principal.user.is_admin
    return [UserOut.of(user, full=full) for user in await context.users.list(principal.id)]


@router.post(
    "",
    status_code=201,
    summary="Create a user",
    description=ADMINS_ONLY + " Creates the user's private default drawer, too.",
    response_model=UserOut,
    responses=problem_responses(401, 403, 409, 422),
)
async def create_user(body: UserCreate, user: CurrentUser, context: Context) -> UserOut:
    password = None if body.password is None else body.password.get_secret_value()
    return UserOut.of(await context.users.create_user(user, body.username, body.role, password))


@router.get(
    "/{id}",
    summary="A user",
    description=(
        "Admins see the account with its sign-in methods; others only active users' id and name."
    ),
    response_model=AccountOut | UserOut,
    response_model_exclude_none=True,
    responses=problem_responses(401, 404, 422),
)
async def get_user(id: UUID, principal: Authenticated, context: Context) -> AccountOut | UserOut:
    if principal.user.is_admin:
        return _account(await context.users.account(principal.id, UserId(id)))
    return UserOut.of(await context.users.get(principal.id, UserId(id)), full=False)


@router.patch(
    "/{id}",
    summary="Change role or state",
    description=(
        ADMINS_ONLY + " Deactivating ends the user's sessions and blocks their API tokens. "
        "The last active admin stays an active admin (409)."
    ),
    response_model=UserOut,
    responses=problem_responses(401, 403, 404, 409, 422),
)
async def update_user(id: UUID, body: UserPatch, user: CurrentUser, context: Context) -> UserOut:
    changed = (await context.users.account(user, UserId(id))).user  # admins only
    if body.role is not None:
        changed = await context.users.change_role(user, UserId(id), body.role)
    if body.active is not None:
        changed = await context.users.set_active(user, UserId(id), body.active)
    return UserOut.of(changed)


@router.post(
    "/{id}/password",
    status_code=204,
    summary="Reset a user's password",
    description=ADMINS_ONLY
    + " Ends all the user's sessions; API tokens stay unless `revoke_tokens`.",
    responses=problem_responses(401, 403, 404, 422),
)
async def reset_password(
    id: UUID, body: PasswordReset, user: CurrentUser, context: Context
) -> None:
    await context.users.reset_password(
        user, UserId(id), body.password.get_secret_value(), revoke_tokens=body.revoke_tokens
    )


@router.delete(
    "/{id}/totp",
    status_code=204,
    summary="Turn a user's TOTP off",
    description=ADMINS_ONLY + " For a user who lost authenticator and recovery codes.",
    responses=problem_responses(401, 403, 404, 422),
)
async def disable_totp(id: UUID, user: CurrentUser, context: Context) -> None:
    await context.users.disable_totp(user, UserId(id))


@router.delete(
    "/{id}/oidc",
    summary="Remove a user's links to the identity provider",
    description=ADMINS_ONLY,
    response_model=Removed,
    responses=problem_responses(401, 403, 404, 422),
)
async def unlink(id: UUID, user: CurrentUser, context: Context) -> Removed:
    return Removed(removed=await context.users.unlink_external_identities(user, UserId(id)))


@router.delete(
    "/{id}",
    status_code=204,
    summary="Delete a user",
    description=(
        ADMINS_ONLY + " Only a user who owns no documents and whose drawers are empty (409); "
        "removes their drawers, the shares to them and their sign-in data."
    ),
    responses=problem_responses(401, 403, 404, 409, 422),
)
async def delete_user(id: UUID, user: CurrentUser, context: Context) -> None:
    await context.users.delete_user(user, UserId(id))
