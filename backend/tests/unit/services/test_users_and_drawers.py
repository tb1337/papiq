import pytest

from papiq.core.domain.drawers import DEFAULT_DRAWER_NAME, ShareLevel
from papiq.core.domain.errors import (
    AuthenticationError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from papiq.core.domain.ids import DrawerId, UserId, new_id
from papiq.core.domain.users import Role
from tests.builders import incoming
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


async def test_unknown_or_deactivated_actor_is_not_authenticated(world: World) -> None:
    with pytest.raises(AuthenticationError):
        await world.users.create_user(UserId(new_id()), "x", Role.USER)
    admin = await world.user(role=Role.ADMIN)
    async with world.uow() as uow:
        stored = await uow.users.get(admin.id)
        stored.active = False
        await uow.users.update(stored)
        await uow.commit()
    with pytest.raises(AuthenticationError):
        await world.users.create_user(admin.id, "x", Role.USER)


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


async def test_drawers_are_read_and_deleted(world: World) -> None:
    owner, reader, stranger = await world.user(), await world.user(), await world.user()
    drawer = await world.drawers.create(owner.id, "Archive")
    await world.drawers.share(owner.id, drawer.id, reader.id, ShareLevel.READ)
    assert (await world.drawers.get(reader.id, drawer.id)).id == drawer.id
    with pytest.raises(NotFoundError):
        await world.drawers.get(stranger.id, drawer.id)
    with pytest.raises(PermissionDeniedError):
        await world.drawers.delete(reader.id, drawer.id)
    with pytest.raises(NotFoundError):
        await world.drawers.delete(stranger.id, drawer.id)
    with pytest.raises(ConflictError):
        await world.drawers.delete(owner.id, (await world.default_drawer(owner)).id)
    await world.pipeline().receive(
        owner.id, incoming(b"%PDF-1.7 x"), filename="x.pdf", drawer=drawer.id
    )
    with pytest.raises(ConflictError):
        await world.drawers.delete(owner.id, drawer.id)
    empty = await world.drawers.create(owner.id, "Empty")
    await world.drawers.delete(owner.id, empty.id)
    with pytest.raises(NotFoundError):
        await world.drawers.get(owner.id, empty.id)


async def test_drawers_are_not_shared_with_deactivated_users(world: World) -> None:
    """M4-10: a share would take effect unnoticed when the user is activated again."""
    admin, owner, gone = await world.user(role=Role.ADMIN), await world.user(), await world.user()
    drawer = await world.drawers.create(owner.id, "Household")
    await world.users.set_active(admin.id, gone.id, False)
    with pytest.raises(NotFoundError):
        await world.drawers.share(owner.id, drawer.id, gone.id, ShareLevel.READ)
    with pytest.raises(NotFoundError):
        await world.drawers.share(owner.id, drawer.id, UserId(new_id()), ShareLevel.READ)
