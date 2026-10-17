from papiq.core.domain.errors import ConflictError, NotFoundError, PermissionDeniedError
from papiq.core.domain.ids import SpaceId, UserId
from papiq.core.domain.permissions import can_manage_space
from papiq.core.domain.spaces import ShareLevel, Space
from papiq.core.domain.users import User
from papiq.core.domain.validation import name_key
from papiq.core.ports import Clock, UnitOfWork, UnitOfWorkFactory
from papiq.core.services._access import load_actor, visible_space
from papiq.core.services.rules.references import disable_rules, refers_to


class SpaceService:
    def __init__(self, uow: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    async def list(self, actor: UserId) -> list[Space]:
        """Spaces the user owns or that are shared with them; every space for admins."""
        async with self._uow() as uow:
            user = await load_actor(uow, actor)
            if user.is_active_admin:
                return await uow.spaces.list_all()
            return await uow.spaces.list_accessible(actor)

    async def get(self, actor: UserId, id: SpaceId) -> Space:
        """A space the user owns or that is shared with them, any space for admins;
        NotFoundError otherwise."""
        async with self._uow() as uow:
            return await visible_space(uow, await load_actor(uow, actor), id)

    async def delete(self, actor: UserId, id: SpaceId) -> None:
        """Owner or admin; not the default space, and only while it holds no documents. Rules
        that file into it are disabled."""
        async with self._uow() as uow:
            space = await _managed_space(uow, await load_actor(uow, actor), id)
            if space.is_default:
                raise ConflictError("the default space cannot be deleted")
            if await uow.documents.exists(space=id):
                raise ConflictError(f"space '{space.name}' is not empty")
            await uow.spaces.remove(id)
            await disable_rules(
                uow,
                self._clock.now(),
                f"space '{space.name}' was deleted",
                refers_to(lambda refs: refs.spaces, id),
            )
            await uow.commit()

    async def create(self, actor: UserId, name: str, *, owner: UserId | None = None) -> Space:
        """A space owned by the caller; an admin may create one for another active user."""
        async with self._uow() as uow:
            user = await load_actor(uow, actor)
            if owner is not None and owner != actor:
                if not user.is_active_admin:
                    raise PermissionDeniedError("only admins create spaces for other users")
                target = await uow.users.find(owner)
                if target is None or not target.active:
                    raise NotFoundError("user", owner)
                user = target
            space = Space.create(owner_id=user.id, name=name, now=self._clock.now())
            await _check_name_free(uow, space)
            await uow.spaces.add(space)
            await uow.commit()
        return space

    async def rename(self, actor: UserId, id: SpaceId, name: str) -> Space:
        async with self._uow() as uow:
            space = await _managed_space(uow, await load_actor(uow, actor), id)
            space.rename(name)
            await _check_name_free(uow, space)
            await uow.spaces.update(space)
            await uow.commit()
        return space

    async def share(self, actor: UserId, id: SpaceId, user: UserId, level: ShareLevel) -> Space:
        async with self._uow() as uow:
            space = await _managed_space(uow, await load_actor(uow, actor), id)
            recipient = await uow.users.find(user)
            if recipient is None or not recipient.active:
                # As in the user list: deactivated users are not there for others.
                raise NotFoundError("user", user)
            space.share(user, level)
            await uow.spaces.update(space)
            await uow.commit()
        return space

    async def unshare(self, actor: UserId, id: SpaceId, user: UserId) -> Space:
        async with self._uow() as uow:
            space = await _managed_space(uow, await load_actor(uow, actor), id)
            space.unshare(user)
            await uow.spaces.update(space)
            await uow.commit()
        return space


async def _managed_space(uow: UnitOfWork, user: User, id: SpaceId) -> Space:
    space = await visible_space(uow, user, id)
    if not can_manage_space(user, space):
        raise PermissionDeniedError(f"only the owner and admins manage space {id}")
    return space


async def _check_name_free(uow: UnitOfWork, space: Space) -> None:
    for other in await uow.spaces.list_accessible(space.owner_id):
        if (
            other.id != space.id
            and other.owner_id == space.owner_id
            and name_key(other.name) == name_key(space.name)
        ):
            raise ConflictError(f"space '{space.name}' already exists")
