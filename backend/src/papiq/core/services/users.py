from papiq.core.domain.drawers import Drawer
from papiq.core.domain.errors import ConflictError, PermissionDeniedError
from papiq.core.domain.ids import UserId
from papiq.core.domain.permissions import can_manage_users
from papiq.core.domain.users import Role, User
from papiq.core.ports import Clock, UnitOfWorkFactory
from papiq.core.services._access import load_actor


class UserService:
    def __init__(self, uow: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    async def create_user(self, actor: UserId, username: str, role: Role) -> User:
        """Admins only. Creates the user together with their private default drawer."""
        now = self._clock.now()
        async with self._uow() as uow:
            if not can_manage_users(await load_actor(uow, actor)):
                raise PermissionDeniedError("only admins create users")
            user = User.create(username=username, role=role, now=now)
            if await uow.users.find_by_username(user.username) is not None:
                raise ConflictError(f"username '{user.username}' is taken")
            await uow.users.add(user)
            await uow.drawers.add(Drawer.create_default(owner_id=user.id, now=now))
            await uow.commit()
        return user
