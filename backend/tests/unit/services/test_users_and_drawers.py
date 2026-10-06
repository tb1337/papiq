import pytest

from papiq.core.domain.drawers import DEFAULT_DRAWER_NAME, ShareLevel
from papiq.core.domain.errors import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from papiq.core.domain.ids import DrawerId, UserId, new_id
from papiq.core.domain.users import Role
from tests.unit.services.conftest import World


async def test_admin_creates_user_with_default_drawer(world: World) -> None:
    admin = await world.user(role=Role.ADMIN)
    user = await world.users.create_user(admin.id, "dana", Role.USER)
    async with world.uow() as uow:
        assert await uow.users.get(user.id) == user
        default = await uow.drawers.get_default(user.id)
    assert default.is_default
    assert default.name == DEFAULT_DRAWER_NAME


async def test_only_admins_create_users(world: World) -> None:
    user = await world.user()
    with pytest.raises(PermissionDeniedError):
        await world.users.create_user(user.id, "eve", Role.USER)


async def test_usernames_are_unique(world: World) -> None:
    admin = await world.user("root", role=Role.ADMIN)
    with pytest.raises(ConflictError):
        await world.users.create_user(admin.id, "ROOT", Role.USER)


async def test_unknown_actor(world: World) -> None:
    with pytest.raises(NotFoundError):
        await world.users.create_user(UserId(new_id()), "x", Role.USER)


async def test_owner_creates_renames_and_shares_drawers(world: World) -> None:
    owner, other = await world.user(), await world.user()
    drawer = await world.drawers.create(owner.id, "Household")
    renamed = await world.drawers.rename(owner.id, drawer.id, "Home")
    assert renamed.name == "Home"
    shared = await world.drawers.share(owner.id, drawer.id, other.id, ShareLevel.READ)
    assert shared.shares == {other.id: ShareLevel.READ}
    assert {d.id for d in await world.drawers.list(other.id)} == {
        drawer.id,
        (await world.default_drawer(other)).id,
    }
    unshared = await world.drawers.unshare(owner.id, drawer.id, other.id)
    assert unshared.shares == {}


async def test_drawer_names_are_unique_per_owner(world: World) -> None:
    owner, other = await world.user(), await world.user()
    first = await world.drawers.create(owner.id, "Taxes")
    await world.drawers.create(other.id, "Taxes")
    with pytest.raises(ConflictError):
        await world.drawers.create(owner.id, "taxes")
    second = await world.drawers.create(owner.id, "Insurance")
    with pytest.raises(ConflictError):
        await world.drawers.rename(owner.id, second.id, "TAXES")
    assert (await world.drawers.rename(owner.id, first.id, "TAXES")).name == "TAXES"


async def test_only_the_owner_manages_a_drawer(world: World) -> None:
    owner, writer, stranger = await world.user(), await world.user(), await world.user()
    drawer = await world.drawers.create(owner.id, "Household")
    await world.drawers.share(owner.id, drawer.id, writer.id, ShareLevel.READ_WRITE)
    with pytest.raises(PermissionDeniedError):
        await world.drawers.rename(writer.id, drawer.id, "Mine")
    with pytest.raises(PermissionDeniedError):
        await world.drawers.share(writer.id, drawer.id, stranger.id, ShareLevel.READ)
    with pytest.raises(NotFoundError):
        await world.drawers.rename(stranger.id, drawer.id, "Mine")
    with pytest.raises(NotFoundError):
        await world.drawers.rename(owner.id, DrawerId(new_id()), "Mine")


async def test_sharing_rules(world: World) -> None:
    owner = await world.user()
    default = await world.default_drawer(owner)
    other = await world.user()
    with pytest.raises(ValidationError, match="default drawer"):
        await world.drawers.share(owner.id, default.id, other.id, ShareLevel.READ)
    drawer = await world.drawers.create(owner.id, "Household")
    with pytest.raises(NotFoundError):
        await world.drawers.share(owner.id, drawer.id, UserId(new_id()), ShareLevel.READ)
