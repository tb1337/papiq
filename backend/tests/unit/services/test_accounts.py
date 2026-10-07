"""Account management by admins and the first admin from configuration."""

import pytest

from papiq.core.domain.drawers import ShareLevel
from papiq.core.domain.errors import (
    AuthenticationError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    SecondFactorRequiredError,
    ValidationError,
)
from papiq.core.domain.identity import ExternalIdentity, TokenScope
from papiq.core.domain.ids import UserId, new_id
from papiq.core.domain.users import Role
from tests import builders
from tests.builders import NOW, PASSWORD
from tests.unit.services.conftest import World

NEW_PASSWORD = "another long passphrase"


async def test_admins_create_users_with_password(world: World) -> None:
    admin = await world.account("root", Role.ADMIN)
    user = await world.users.create_user(admin.id, "alice", Role.USER, PASSWORD)
    signed_in = await world.auth.login("alice", PASSWORD)
    assert signed_in.user == user
    assert (await world.default_drawer(user)).is_default
    for weak in ("short", "alice"):
        with pytest.raises(ValidationError):
            await world.users.create_user(admin.id, "bob", Role.USER, weak)
    nobody = await world.users.create_user(admin.id, "carol", Role.USER)
    account = await world.users.account(admin.id, nobody.id)
    assert not account.has_password and not account.totp_enabled


async def test_only_admins_manage_accounts(world: World) -> None:
    user = await world.account("alice")
    other = await world.account("bob")
    calls = [
        world.users.create_user(user.id, "x", Role.USER),
        world.users.change_role(user.id, other.id, Role.ADMIN),
        world.users.set_active(user.id, other.id, False),
        world.users.reset_password(user.id, other.id, NEW_PASSWORD),
        world.users.disable_totp(user.id, other.id),
        world.users.unlink_external_identities(user.id, other.id),
        world.users.delete_user(user.id, other.id),
        world.users.account(user.id, other.id),
    ]
    for call in calls:
        with pytest.raises(PermissionDeniedError):
            await call


async def test_users_see_active_users_and_admins_all(world: World) -> None:
    admin = await world.account("root", Role.ADMIN)
    alice, bob = await world.account("alice"), await world.account("Bob")
    await world.users.set_active(admin.id, bob.id, False)
    assert [u.username for u in await world.users.list(alice.id)] == ["alice", "root"]
    assert [u.username for u in await world.users.list(admin.id)] == ["alice", "Bob", "root"]
    with pytest.raises(NotFoundError):
        await world.users.get(alice.id, bob.id)
    assert (await world.users.get(admin.id, bob.id)).active is False


async def test_the_last_active_admin_stays(world: World) -> None:
    admin = await world.account("root", Role.ADMIN)
    for call in (
        world.users.change_role(admin.id, admin.id, Role.USER),
        world.users.set_active(admin.id, admin.id, False),
        world.users.delete_user(admin.id, admin.id),
    ):
        with pytest.raises(ConflictError):
            await call
    second = await world.account("second", Role.ADMIN)
    inactive = await world.account("inactive", Role.ADMIN)
    await world.users.set_active(admin.id, inactive.id, False)
    await world.users.change_role(second.id, admin.id, Role.USER)
    with pytest.raises(ConflictError):  # the inactive admin does not count
        await world.users.change_role(second.id, second.id, Role.USER)


async def test_taking_admin_rights_writes_the_remaining_admins(world: World) -> None:
    """So that a concurrent change relying on them fails its version check."""
    admin = await world.account("root", Role.ADMIN)
    second = await world.account("second", Role.ADMIN)
    await world.users.change_role(admin.id, admin.id, Role.USER)
    async with world.uow() as uow:
        assert (await uow.users.get(second.id)).version == second.version + 1


async def test_role_changes_apply_at_once(world: World) -> None:
    admin = await world.account("root", Role.ADMIN)
    user = await world.account("alice")
    signed_in = await world.auth.login("alice", PASSWORD)
    await world.users.change_role(admin.id, user.id, Role.ADMIN)
    principal = await world.auth.authenticate_session(signed_in.token)
    assert principal.user.is_admin
    await world.master_data.create_tag(user.id, "now possible")


async def test_deactivation_ends_sessions_and_refuses_tokens(world: World) -> None:
    admin = await world.account("root", Role.ADMIN)
    user = await world.account("alice")
    signed_in = await world.auth.login("alice", PASSWORD)
    _, token = await world.auth.create_api_token(user.id, "cli", TokenScope.READ)

    await world.users.set_active(admin.id, user.id, False)

    async with world.uow() as uow:
        assert await uow.sessions.find_by_token(signed_in.session.token_hash) is None
    with pytest.raises(AuthenticationError):
        await world.auth.authenticate_token(token)
    with pytest.raises(AuthenticationError):
        await world.auth.login("alice", PASSWORD)
    with pytest.raises(AuthenticationError):
        await world.drawers.list(user.id)

    await world.users.set_active(admin.id, user.id, True)
    await world.auth.authenticate_token(token)
    await world.auth.login("alice", PASSWORD)


async def test_password_reset_by_an_admin(world: World) -> None:
    admin = await world.account("root", Role.ADMIN)
    user = await world.account("alice")
    signed_in = await world.auth.login("alice", PASSWORD)
    _, token = await world.auth.create_api_token(user.id, "cli", TokenScope.READ)

    await world.users.reset_password(admin.id, user.id, NEW_PASSWORD)
    with pytest.raises(AuthenticationError):
        await world.auth.authenticate_session(signed_in.token)
    await world.auth.authenticate_token(token)
    await world.auth.login("alice", NEW_PASSWORD)

    await world.users.reset_password(admin.id, user.id, PASSWORD, revoke_tokens=True)
    with pytest.raises(AuthenticationError):
        await world.auth.authenticate_token(token)
    with pytest.raises(ValidationError):
        await world.users.reset_password(admin.id, user.id, "short")


async def test_admins_turn_totp_off(world: World) -> None:
    admin = await world.account("root", Role.ADMIN)
    user = await world.account("alice")
    setup = await world.auth.begin_totp(user.id)
    await world.auth.confirm_totp(user.id, world.totp.code(setup.secret, world.clock.now()))
    with pytest.raises(SecondFactorRequiredError):
        await world.auth.login("alice", PASSWORD)
    assert (await world.users.account(admin.id, user.id)).totp_enabled
    await world.users.disable_totp(admin.id, user.id)
    await world.auth.login("alice", PASSWORD)


async def test_admins_unlink_identity_providers(world: World) -> None:
    admin = await world.account("root", Role.ADMIN)
    user = await world.account("alice")
    async with world.uow() as uow:
        await uow.external_identities.add(
            ExternalIdentity.link(issuer="https://idp", subject="s", user_id=user.id, now=NOW)
        )
        await uow.commit()
    assert len((await world.users.account(admin.id, user.id)).external_identities) == 1
    assert await world.users.unlink_external_identities(admin.id, user.id) == 1
    assert (await world.users.account(admin.id, user.id)).external_identities == []


async def test_deleting_a_user_without_documents(world: World) -> None:
    admin = await world.account("root", Role.ADMIN)
    user = await world.account("alice")
    friend = await world.account("bob")
    own = await world.drawers.create(user.id, "Mine")
    shared = await world.drawers.create(friend.id, "Shared")
    await world.drawers.share(friend.id, shared.id, user.id, ShareLevel.READ)
    await world.auth.create_api_token(user.id, "cli", TokenScope.READ)
    await world.auth.login("alice", PASSWORD)

    await world.users.delete_user(admin.id, user.id)

    async with world.uow() as uow:
        assert await uow.users.find(user.id) is None
        assert await uow.drawers.find(own.id) is None
        assert (await uow.drawers.get(shared.id)).shares == {}
        assert await uow.credentials.find(user.id) is None
        assert await uow.api_tokens.list_for(user.id) == []
    with pytest.raises(AuthenticationError):
        await world.auth.login("alice", PASSWORD)
    with pytest.raises(NotFoundError):
        await world.users.delete_user(admin.id, UserId(new_id()))


async def test_users_with_documents_are_not_deleted(world: World) -> None:
    admin = await world.account("root", Role.ADMIN)
    owner, guest = await world.account("alice"), await world.account("bob")
    drawer = await world.drawers.create(owner.id, "Household")
    await world.drawers.share(owner.id, drawer.id, guest.id, ShareLevel.READ_WRITE)
    async with world.uow() as uow:
        await uow.documents.add(builders.processed(guest, drawer))  # guest's file, owner's drawer
        await uow.commit()
    with pytest.raises(ConflictError, match="owns documents"):
        await world.users.delete_user(admin.id, guest.id)
    with pytest.raises(ConflictError, match="not empty"):
        await world.users.delete_user(admin.id, owner.id)


# --- first admin --------------------------------------------------------------------------------


async def test_the_first_admin_is_created_once(world: World) -> None:
    created = await world.users.bootstrap_admin("root", PASSWORD)
    assert created is not None and created.is_admin
    await world.auth.login("root", PASSWORD)
    assert (await world.default_drawer(created)).is_default
    # Later starts change nothing, also not the password.
    assert await world.users.bootstrap_admin("root", NEW_PASSWORD) is None
    assert await world.users.bootstrap_admin("other", NEW_PASSWORD) is None
    with pytest.raises(AuthenticationError):
        await world.auth.login("root", NEW_PASSWORD)


async def test_the_first_admin_does_not_take_over_a_user(world: World) -> None:
    await world.account("alice")
    with pytest.raises(ConflictError):
        await world.users.bootstrap_admin("Alice", NEW_PASSWORD)
    with pytest.raises(ValidationError):
        await world.users.bootstrap_admin("root", "short")
    await world.auth.login("alice", PASSWORD)
