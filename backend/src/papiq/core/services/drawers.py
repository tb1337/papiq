from papiq.core.domain.drawers import Drawer, ShareLevel
from papiq.core.domain.errors import ConflictError, PermissionDeniedError
from papiq.core.domain.ids import DrawerId, UserId
from papiq.core.domain.permissions import can_manage_drawer
from papiq.core.domain.users import User
from papiq.core.domain.validation import name_key
from papiq.core.ports import Clock, UnitOfWork, UnitOfWorkFactory
from papiq.core.services._access import load_actor, visible_drawer


class DrawerService:
    def __init__(self, uow: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    async def list(self, actor: UserId) -> list[Drawer]:
        """Drawers the user owns or that are shared with them."""
        async with self._uow() as uow:
            await load_actor(uow, actor)
            return await uow.drawers.list_accessible(actor)

    async def get(self, actor: UserId, id: DrawerId) -> Drawer:
        """A drawer the user owns or that is shared with them; NotFoundError otherwise."""
        async with self._uow() as uow:
            return await visible_drawer(uow, await load_actor(uow, actor), id)

    async def delete(self, actor: UserId, id: DrawerId) -> None:
        """Owner only; not the default drawer, and only while it holds no documents."""
        async with self._uow() as uow:
            drawer = await _managed_drawer(uow, await load_actor(uow, actor), id)
            if drawer.is_default:
                raise ConflictError("the default drawer cannot be deleted")
            if await uow.documents.exists(drawer=id):
                raise ConflictError(f"drawer '{drawer.name}' is not empty")
            await uow.drawers.remove(id)
            await uow.commit()

    async def create(self, actor: UserId, name: str) -> Drawer:
        async with self._uow() as uow:
            user = await load_actor(uow, actor)
            drawer = Drawer.create(owner_id=user.id, name=name, now=self._clock.now())
            await _check_name_free(uow, drawer)
            await uow.drawers.add(drawer)
            await uow.commit()
        return drawer

    async def rename(self, actor: UserId, id: DrawerId, name: str) -> Drawer:
        async with self._uow() as uow:
            drawer = await _managed_drawer(uow, await load_actor(uow, actor), id)
            drawer.rename(name)
            await _check_name_free(uow, drawer)
            await uow.drawers.update(drawer)
            await uow.commit()
        return drawer

    async def share(self, actor: UserId, id: DrawerId, user: UserId, level: ShareLevel) -> Drawer:
        async with self._uow() as uow:
            drawer = await _managed_drawer(uow, await load_actor(uow, actor), id)
            await uow.users.get(user)
            drawer.share(user, level)
            await uow.drawers.update(drawer)
            await uow.commit()
        return drawer

    async def unshare(self, actor: UserId, id: DrawerId, user: UserId) -> Drawer:
        async with self._uow() as uow:
            drawer = await _managed_drawer(uow, await load_actor(uow, actor), id)
            drawer.unshare(user)
            await uow.drawers.update(drawer)
            await uow.commit()
        return drawer


async def _managed_drawer(uow: UnitOfWork, user: User, id: DrawerId) -> Drawer:
    drawer = await visible_drawer(uow, user, id)
    if not can_manage_drawer(user, drawer):
        raise PermissionDeniedError(f"only the owner manages drawer {id}")
    return drawer


async def _check_name_free(uow: UnitOfWork, drawer: Drawer) -> None:
    for other in await uow.drawers.list_accessible(drawer.owner_id):
        if (
            other.id != drawer.id
            and other.owner_id == drawer.owner_id
            and name_key(other.name) == name_key(drawer.name)
        ):
            raise ConflictError(f"drawer '{drawer.name}' already exists")
