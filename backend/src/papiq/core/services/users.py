"""Accounts: created and managed by admins; the first admin comes from configuration.

The last active admin can be neither demoted, deactivated nor deleted. Two admins demoting
each other at the same time must not both succeed: a change that takes away admin rights also
writes the other active admins (version check), so one of two such transactions fails.
"""

import logging
from dataclasses import dataclass
from datetime import datetime

from papiq.core.domain.drawers import Drawer
from papiq.core.domain.errors import ConflictError, NotFoundError, PermissionDeniedError
from papiq.core.domain.identity import Credential, ExternalIdentity, check_new_password
from papiq.core.domain.ids import UserId
from papiq.core.domain.permissions import can_manage_users
from papiq.core.domain.users import Role, User
from papiq.core.ports import Clock, PasswordHasher, UnitOfWork, UnitOfWorkFactory
from papiq.core.services._access import load_actor
from papiq.core.services.rules.references import disable_rules, refers_to

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Account:
    """A user with the state of their sign-in methods, for admins."""

    user: User
    has_password: bool
    totp_enabled: bool
    external_identities: list[ExternalIdentity]


class UserService:
    def __init__(
        self, uow: UnitOfWorkFactory, clock: Clock, hasher: PasswordHasher | None = None
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._hasher = hasher

    # --- reading --------------------------------------------------------------------------------

    async def list(self, actor: UserId) -> list[User]:
        """Admins see all users; others see the active ones (to share drawers with them)."""
        async with self._uow() as uow:
            user = await load_actor(uow, actor)
            users = await uow.users.list_all()
        visible = users if can_manage_users(user) else [u for u in users if u.active]
        return sorted(visible, key=lambda u: u.username.casefold())

    async def get(self, actor: UserId, id: UserId) -> User:
        """Like `list`: a deactivated user is not found for others than admins."""
        async with self._uow() as uow:
            user = await load_actor(uow, actor)
            found = await uow.users.find(id)
        if found is None or not (found.active or can_manage_users(user)):
            raise NotFoundError("user", id)
        return found

    async def own_account(self, actor: UserId) -> Account:
        """The caller's own account."""
        async with self._uow() as uow:
            return await _account(uow, await load_actor(uow, actor))

    async def account(self, actor: UserId, id: UserId) -> Account:
        """Admins only."""
        async with self._uow() as uow:
            await _require_admin(uow, actor)
            return await _account(uow, await uow.users.get(id))

    # --- changes by admins ----------------------------------------------------------------------

    async def create_user(
        self, actor: UserId, username: str, role: Role, password: str | None = None
    ) -> User:
        """Admins only. Creates the user together with their private default drawer and, if
        given, a password (which must meet the policy)."""
        password_hash = await self._hash(username, password)
        now = self._clock.now()
        async with self._uow() as uow:
            await _require_admin(uow, actor)
            user = await add_user(uow, username, role, now)
            if password_hash is not None:
                await uow.credentials.add(
                    Credential(
                        user_id=user.id, password_hash=password_hash, password_changed_at=now
                    )
                )
            await uow.commit()
        log.info("user created", extra={"user_id": str(user.id), "role": role.value})
        return user

    async def change_role(self, actor: UserId, id: UserId, role: Role) -> User:
        return await self.update(actor, id, role=role)

    async def set_active(self, actor: UserId, id: UserId, active: bool) -> User:
        return await self.update(actor, id, active=active)

    async def update(
        self, actor: UserId, id: UserId, *, role: Role | None = None, active: bool | None = None
    ) -> User:
        """Admins only; role and state in one transaction. Rights follow the role at once,
        since every request reads the user anew; sessions and tokens stay. Deactivating ends
        the user's sessions; their API tokens are refused while the account is inactive."""
        async with self._uow() as uow:
            await _require_admin(uow, actor)
            user = await uow.users.get(id)
            new_role = user.role if role is None else role
            new_active = user.active if active is None else active
            if (new_role, new_active) != (user.role, user.active):
                if user.is_active_admin and not (new_active and new_role is Role.ADMIN):
                    await _keep_an_admin(uow, user)
                user.role, user.active = new_role, new_active
                await uow.users.update(user)
                if not new_active:
                    await uow.sessions.remove_for_user(id)
                await uow.commit()
        log.info(
            "account changed",
            extra={"user_id": str(id), "role": user.role.value, "active": user.active},
        )
        return user

    async def reset_password(
        self, actor: UserId, id: UserId, password: str, *, revoke_tokens: bool = False
    ) -> None:
        """Admins only, not for their own account (that is `AuthService.change_password`, which
        needs the current password). All sessions of the user end; API tokens stay unless
        `revoke_tokens`."""
        _not_own(actor, id, "reset their own password here")
        async with self._uow() as uow:
            await _require_admin(uow, actor)
            user = await uow.users.get(id)
        password_hash = await self._hash(user.username, password)
        assert password_hash is not None
        now = self._clock.now()
        async with self._uow() as uow:
            await _require_admin(uow, actor)
            credential = await uow.credentials.find(id)
            if credential is None:
                await uow.credentials.add(
                    Credential(user_id=id, password_hash=password_hash, password_changed_at=now)
                )
            else:
                credential.password_hash = password_hash
                credential.password_changed_at = now
                await uow.credentials.update(credential)
            await uow.sessions.remove_for_user(id)
            if revoke_tokens:
                await uow.api_tokens.remove_for_user(id)
            await uow.commit()
        log.info("password reset", extra={"user_id": str(id), "tokens_revoked": revoke_tokens})

    async def disable_totp(self, actor: UserId, id: UserId) -> None:
        """Admins only, for a user who lost their authenticator and recovery codes; not for
        their own account (that is `AuthService.disable_totp`, which needs a code)."""
        _not_own(actor, id, "turn off their own TOTP here")
        async with self._uow() as uow:
            await _require_admin(uow, actor)
            await uow.users.get(id)
            credential = await uow.credentials.find(id)
            if credential is not None and credential.totp is not None:
                credential.totp = None
                credential.recovery_codes = set()
                await uow.credentials.update(credential)
                await uow.commit()
        log.info("TOTP turned off by an admin", extra={"user_id": str(id)})

    async def unlink_external_identities(self, actor: UserId, id: UserId) -> int:
        """Admins only: remove the user's links to identity providers."""
        async with self._uow() as uow:
            await _require_admin(uow, actor)
            await uow.users.get(id)
            removed = await uow.external_identities.remove_for_user(id)
            await uow.commit()
        return removed

    async def delete_user(self, actor: UserId, id: UserId) -> None:
        """Admins only, and only for a user who owns no documents and whose drawers are empty.
        Removes their drawers, the shares to them, their rules and all their sign-in data.
        Other users' rules that file into the removed drawers are disabled."""
        async with self._uow() as uow:
            await _require_admin(uow, actor)
            user = await uow.users.get(id)
            if user.is_active_admin:
                await _keep_an_admin(uow, user)
            if await uow.documents.exists(owner=id):
                raise ConflictError("the user owns documents; delete or move them first")
            removed: list[Drawer] = []
            for drawer in await uow.drawers.list_accessible(id):
                if drawer.owner_id == id:
                    if await uow.documents.exists(drawer=drawer.id):
                        raise ConflictError(f"the user's drawer '{drawer.name}' is not empty")
                    await uow.drawers.remove(drawer.id)
                    removed.append(drawer)
                else:
                    drawer.unshare(id)
                    await uow.drawers.update(drawer)
            await uow.rules.remove_for_owner(id)
            for drawer in removed:
                await disable_rules(
                    uow,
                    self._clock.now(),
                    f"drawer '{drawer.name}' was deleted",
                    refers_to(lambda refs: refs.drawers, drawer.id),
                )
            await uow.sessions.remove_for_user(id)
            await uow.api_tokens.remove_for_user(id)
            await uow.external_identities.remove_for_user(id)
            await uow.credentials.remove(id)
            await uow.users.remove(id)
            await uow.commit()
        log.info("user deleted", extra={"user_id": str(id)})

    # --- first admin ----------------------------------------------------------------------------

    async def bootstrap_admin(self, username: str, password: str) -> User | None:
        """Create the first admin, if there is no admin yet; otherwise do nothing and return
        None (an existing account is never changed). ConflictError if the name belongs to a
        user who is not an admin."""
        async with self._uow() as uow:
            if any(user.is_admin for user in await uow.users.list_all()):
                return None
        password_hash = await self._hash(username, password)
        assert password_hash is not None
        now = self._clock.now()
        async with self._uow() as uow:
            if any(user.is_admin for user in await uow.users.list_all()):
                return None
            if await uow.users.find_by_username(username) is not None:
                raise ConflictError(f"username '{username}' belongs to a user who is no admin")
            user = await add_user(uow, username, Role.ADMIN, now)
            await uow.credentials.add(
                Credential(user_id=user.id, password_hash=password_hash, password_changed_at=now)
            )
            await uow.commit()
        log.info("first admin created", extra={"user_id": str(user.id)})
        return user

    async def _hash(self, username: str, password: str | None) -> str | None:
        if password is None:
            return None
        if self._hasher is None:
            raise RuntimeError("UserService needs a password hasher to set passwords")
        return await self._hasher.hash(check_new_password(password, username))


def _not_own(actor: UserId, id: UserId, what: str) -> None:
    if actor == id:
        raise PermissionDeniedError(f"admins cannot {what}")


async def _require_admin(uow: UnitOfWork, actor: UserId) -> User:
    user = await load_actor(uow, actor)
    if not can_manage_users(user):
        raise PermissionDeniedError("only admins manage users")
    return user


async def add_user(uow: UnitOfWork, username: str, role: Role, now: datetime) -> User:
    """Add a user with their default drawer; ConflictError if the name is taken."""
    user = User.create(username=username, role=role, now=now)
    if await uow.users.find_by_username(user.username) is not None:
        raise ConflictError(f"username '{user.username}' is taken")
    await uow.users.add(user)
    await uow.drawers.add(Drawer.create_default(owner_id=user.id, now=user.created_at))
    return user


async def _keep_an_admin(uow: UnitOfWork, leaving: User) -> None:
    """ConflictError unless another active admin remains. Writes the remaining ones, so a
    concurrent change that relies on them fails its version check."""
    others = [
        user
        for user in await uow.users.list_all()
        if user.is_active_admin and user.id != leaving.id
    ]
    if not others:
        raise ConflictError("the last active admin must stay an active admin")
    for other in others:
        await uow.users.update(other)


async def _account(uow: UnitOfWork, user: User) -> Account:
    credential = await uow.credentials.find(user.id)
    return Account(
        user=user,
        has_password=credential is not None and credential.password_hash is not None,
        totp_enabled=credential is not None and credential.totp_enabled,
        external_identities=await uow.external_identities.list_for(user.id),
    )
