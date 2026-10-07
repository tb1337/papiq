from datetime import timedelta

import pytest

from papiq.core.domain.errors import (
    AuthenticationError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    SecondFactorRequiredError,
    TooManyAttemptsError,
    ValidationError,
)
from papiq.core.domain.identity import TokenScope, csrf_token, hash_token
from papiq.core.domain.ids import ApiTokenId, new_id
from papiq.core.domain.users import User
from tests.builders import PASSWORD
from tests.unit.services.conftest import World

NEW_PASSWORD = "another long passphrase"


async def set_active(world: World, user: User, active: bool) -> None:
    async with world.uow() as uow:
        stored = await uow.users.get(user.id)
        stored.active = active
        await uow.users.update(stored)
        await uow.commit()


async def enable_totp(world: World, user: User) -> tuple[str, list[str]]:
    setup = await world.auth.begin_totp(user.id)
    codes = await world.auth.confirm_totp(user.id, world.totp.code(setup.secret, world.clock.now()))
    world.clock.advance(timedelta(seconds=30))  # the confirming code is used up
    return setup.secret, codes


# --- password sign-in ---------------------------------------------------------------------------


async def test_login_starts_a_session(world: World) -> None:
    user = await world.account("alice")
    signed_in = await world.auth.login("ALICE", PASSWORD)
    assert signed_in.user.id == user.id
    assert signed_in.session.token_hash == hash_token(signed_in.token)
    assert signed_in.csrf_token == csrf_token(signed_in.token) != signed_in.token
    principal = await world.auth.authenticate_session(signed_in.token)
    assert principal.id == user.id
    assert principal.can_write
    assert principal.session is not None and principal.session.id == signed_in.session.id


async def test_every_login_starts_a_new_session(world: World) -> None:
    await world.account("alice")
    first = await world.auth.login("alice", PASSWORD)
    second = await world.auth.login("alice", PASSWORD)
    assert first.session.id != second.session.id
    assert first.token != second.token


async def test_failures_look_alike_and_take_a_hash_each(world: World) -> None:
    """Unknown user, wrong password and deactivated account: same error, same work."""
    inactive = await world.account("bob")
    await set_active(world, inactive, False)
    await world.account("alice")
    messages = []
    for username, password in [
        ("nobody", PASSWORD),
        ("alice", "wrong password!"),
        ("bob", PASSWORD),
    ]:
        before = world.hasher.verified
        with pytest.raises(AuthenticationError) as info:
            await world.auth.login(username, password)
        assert world.hasher.verified == before + 1
        messages.append(str(info.value))
    assert len(set(messages)) == 1


async def test_passwords_are_normalised(world: World) -> None:
    await world.account("alice", password="Pássword long enough")  # P, a, combining acute
    await world.auth.login("alice", "Pássword long enough")  # precomposed á


async def test_outdated_hashes_are_replaced_at_login(world: World) -> None:
    user = await world.account("alice")
    world.hasher.round = 2
    await world.auth.login("alice", PASSWORD)
    async with world.uow() as uow:
        credential = await uow.credentials.get(user.id)
    assert credential.password_hash is not None
    assert not world.hasher.needs_rehash(credential.password_hash)
    await world.auth.login("alice", PASSWORD)


async def test_an_account_without_password_cannot_log_in(world: World) -> None:
    await world.user("oidc-only")
    with pytest.raises(AuthenticationError):
        await world.auth.login("oidc-only", PASSWORD)


# --- guessing -----------------------------------------------------------------------------------


async def test_failures_per_account_back_off(world: World) -> None:
    await world.account("alice")
    for _ in range(5):
        with pytest.raises(AuthenticationError):
            await world.auth.login("alice", "wrong password!")
    with pytest.raises(AuthenticationError):
        await world.auth.login("alice", "wrong password!")  # sixth: blocks for 1 s
    with pytest.raises(TooManyAttemptsError) as info:
        await world.auth.login("alice", PASSWORD)  # refused even with the right password
    assert info.value.retry_after == timedelta(seconds=1)
    world.clock.advance(timedelta(seconds=1))
    with pytest.raises(AuthenticationError):
        await world.auth.login("alice", "wrong password!")  # seventh: 2 s
    with pytest.raises(TooManyAttemptsError) as info:
        await world.auth.login("alice", PASSWORD)
    assert info.value.retry_after == timedelta(seconds=2)
    world.clock.advance(timedelta(seconds=2))
    await world.auth.login("alice", PASSWORD)
    # Success clears the account's count.
    for _ in range(5):
        with pytest.raises(AuthenticationError):
            await world.auth.login("alice", "wrong password!")
    await world.auth.login("alice", PASSWORD)


async def test_unknown_usernames_are_throttled_like_known_ones(world: World) -> None:
    for _ in range(6):
        with pytest.raises(AuthenticationError):
            await world.auth.login("nobody", "wrong password!")
    with pytest.raises(TooManyAttemptsError):
        await world.auth.login("Nobody", "wrong password!")


async def test_the_backoff_is_capped_at_fifteen_minutes(world: World) -> None:
    await world.account("alice")
    for _ in range(20):
        with pytest.raises((AuthenticationError, TooManyAttemptsError)):
            await world.auth.login("alice", "wrong password!")
        world.clock.advance(timedelta(minutes=15))
    with pytest.raises(AuthenticationError):
        await world.auth.login("alice", "wrong password!")
    with pytest.raises(TooManyAttemptsError) as info:
        await world.auth.login("alice", PASSWORD)
    assert info.value.retry_after == timedelta(minutes=15)


async def test_a_blocked_source_refuses_only_wrong_sign_ins(world: World) -> None:
    """M4-01: thirty failures block the address for wrong sign-ins only (429 instead of 401,
    not counted for the address again); a correct sign-in from there still works."""
    alice = await world.account("alice")
    await world.account("bob")
    for number in range(30):
        with pytest.raises(AuthenticationError):
            await world.auth.login(f"guess-{number}", "wrong password!", source="192.0.2.7")
    with pytest.raises(TooManyAttemptsError) as info:
        await world.auth.login("bob", "wrong password!", source="192.0.2.7")
    assert info.value.retry_after == timedelta(minutes=15)
    async with world.uow() as uow:
        failures = await uow.login_failures.find("source:192.0.2.7")
        assert failures is not None and failures.failures == 30  # not counted again
        bob = await uow.login_failures.find("account:bob")
        assert bob is not None and bob.failures == 1  # the account counts as before
    signed_in = await world.auth.login("alice", PASSWORD, source="192.0.2.7")
    assert signed_in.user.id == alice.id
    await world.auth.login("alice", PASSWORD, source="198.51.100.1")
    world.clock.advance(timedelta(minutes=15))
    with pytest.raises(AuthenticationError):
        await world.auth.login("bob", "wrong password!", source="192.0.2.7")


async def test_failures_from_one_address_do_not_refuse_other_accounts(world: World) -> None:
    """M4-01: thirty wrong guesses from one address must not refuse a correct sign-in to an
    account without failures. Behind a proxy or NAT the address is shared by everyone."""
    await world.account("alice")
    for number in range(30):
        with pytest.raises(AuthenticationError):
            await world.auth.login(f"guess-{number}", "wrong password!", source="203.0.113.1")
    await world.auth.login("alice", PASSWORD, source="203.0.113.1")


async def test_a_blocked_source_refuses_wrong_codes(world: World) -> None:
    user = await world.account("alice")
    secret, _ = await enable_totp(world, user)
    for number in range(30):
        with pytest.raises(AuthenticationError):
            await world.auth.login(f"guess-{number}", "wrong password!", source="192.0.2.7")
    with pytest.raises(TooManyAttemptsError):
        await world.auth.login("alice", PASSWORD, code="000000", source="192.0.2.7")
    code = world.totp.code(secret, world.clock.now())
    await world.auth.login("alice", PASSWORD, code=code, source="192.0.2.7")


# --- sessions -----------------------------------------------------------------------------------


async def test_sessions_end_when_idle_or_too_old(world: World) -> None:
    await world.account("alice")
    signed_in = await world.auth.login("alice", PASSWORD)
    for _ in range(29):  # used daily: lasts until the maximum age
        world.clock.advance(timedelta(hours=23))
        await world.auth.authenticate_session(signed_in.token)
    world.clock.advance(timedelta(days=4))
    with pytest.raises(AuthenticationError):
        await world.auth.authenticate_session(signed_in.token)

    idle = await world.auth.login("alice", PASSWORD)
    world.clock.advance(timedelta(days=1))
    with pytest.raises(AuthenticationError):
        await world.auth.authenticate_session(idle.token)
    async with world.uow() as uow:
        assert await uow.sessions.find_by_token(hash_token(idle.token)) is None


async def test_last_use_is_written_at_most_once_a_minute(world: World) -> None:
    await world.account("alice")
    signed_in = await world.auth.login("alice", PASSWORD)
    world.clock.advance(timedelta(seconds=30))
    await world.auth.authenticate_session(signed_in.token)
    async with world.uow() as uow:
        session = await uow.sessions.find_by_token(signed_in.session.token_hash)
        assert session is not None and session.last_seen_at == signed_in.session.last_seen_at
    world.clock.advance(timedelta(seconds=30))
    await world.auth.authenticate_session(signed_in.token)
    async with world.uow() as uow:
        session = await uow.sessions.find_by_token(signed_in.session.token_hash)
        assert session is not None and session.last_seen_at == world.clock.now()


async def test_logout_and_unknown_tokens(world: World) -> None:
    await world.account("alice")
    signed_in = await world.auth.login("alice", PASSWORD)
    await world.auth.logout(signed_in.session.id)
    for token in (signed_in.token, "made-up", ""):
        with pytest.raises(AuthenticationError):
            await world.auth.authenticate_session(token)


async def test_deactivated_users_lose_their_sessions(world: World) -> None:
    user = await world.account("alice")
    signed_in = await world.auth.login("alice", PASSWORD)
    await set_active(world, user, False)
    with pytest.raises(AuthenticationError):
        await world.auth.authenticate_session(signed_in.token)


async def test_end_other_sessions(world: World) -> None:
    user = await world.account("alice")
    mine = await world.auth.login("alice", PASSWORD)
    other = await world.auth.login("alice", PASSWORD)
    assert await world.auth.end_other_sessions(user.id, keep=mine.session.id) == 1
    await world.auth.authenticate_session(mine.token)
    with pytest.raises(AuthenticationError):
        await world.auth.authenticate_session(other.token)


# --- own password -------------------------------------------------------------------------------


async def test_changing_the_password_ends_other_sessions_and_keeps_tokens(world: World) -> None:
    user = await world.account("alice")
    mine = await world.auth.login("alice", PASSWORD)
    other = await world.auth.login("alice", PASSWORD)
    _, token = await world.auth.create_api_token(user.id, "cli", TokenScope.READ)

    renewed = await world.auth.change_password(
        user.id, PASSWORD, NEW_PASSWORD, session=mine.session.id
    )

    assert renewed is not None and renewed.token != mine.token
    await world.auth.authenticate_session(renewed.token)
    for old in (mine, other):
        with pytest.raises(AuthenticationError):
            await world.auth.authenticate_session(old.token)
    await world.auth.authenticate_token(token)
    with pytest.raises(AuthenticationError):
        await world.auth.login("alice", PASSWORD)
    await world.auth.login("alice", NEW_PASSWORD)


async def test_changing_the_password_can_revoke_tokens(world: World) -> None:
    user = await world.account("alice")
    _, token = await world.auth.create_api_token(user.id, "cli", TokenScope.READ)
    assert (
        await world.auth.change_password(user.id, PASSWORD, NEW_PASSWORD, revoke_tokens=True)
        is None
    )
    with pytest.raises(AuthenticationError):
        await world.auth.authenticate_token(token)


async def test_changing_the_password_needs_the_current_one_and_the_policy(world: World) -> None:
    user = await world.account("alice")
    with pytest.raises(PermissionDeniedError):
        await world.auth.change_password(user.id, "wrong password!", NEW_PASSWORD)
    for weak in ("short", "alice", "ALICE"):
        with pytest.raises(ValidationError):
            await world.auth.change_password(user.id, PASSWORD, weak)
    with pytest.raises(ValidationError):
        await world.auth.change_password(user.id, PASSWORD, "x" * 257)
    await world.auth.login("alice", PASSWORD)


# --- TOTP ---------------------------------------------------------------------------------------


async def test_totp_setup_is_confirmed_by_a_code(world: World) -> None:
    user = await world.account("alice")
    setup = await world.auth.begin_totp(user.id)
    assert setup.secret in setup.uri and "alice" in setup.uri
    async with world.uow() as uow:
        credential = await uow.credentials.get(user.id)
    assert credential.totp is not None and not credential.totp.confirmed
    assert setup.secret.encode() not in credential.totp.secret  # stored encrypted
    await world.auth.login("alice", PASSWORD)  # not required before confirmation

    with pytest.raises(ValidationError):
        await world.auth.confirm_totp(user.id, "000000")
    codes = await world.auth.confirm_totp(user.id, world.totp.code(setup.secret, world.clock.now()))

    assert len(codes) == 10 and len(set(codes)) == 10
    async with world.uow() as uow:
        credential = await uow.credentials.get(user.id)
    assert credential.totp_enabled
    assert all(code not in str(credential.recovery_codes) for code in codes)  # only hashes
    with pytest.raises(ConflictError):
        await world.auth.begin_totp(user.id)


async def test_login_with_totp(world: World) -> None:
    user = await world.account("alice")
    secret, _ = await enable_totp(world, user)
    with pytest.raises(SecondFactorRequiredError):
        await world.auth.login("alice", PASSWORD)
    with pytest.raises(AuthenticationError):
        await world.auth.login("alice", "wrong password!", code="123456")  # password first
    code = world.totp.code(secret, world.clock.now())
    await world.auth.login("alice", PASSWORD, code=code)
    with pytest.raises(AuthenticationError):
        await world.auth.login("alice", PASSWORD, code=code)  # used already
    world.clock.advance(timedelta(seconds=30))
    await world.auth.login("alice", PASSWORD, code=world.totp.code(secret, world.clock.now()))


async def test_codes_of_neighbouring_steps_and_not_older(world: World) -> None:
    user = await world.account("alice")
    secret, _ = await enable_totp(world, user)
    world.clock.advance(timedelta(minutes=1))
    now = world.clock.now()
    late = world.totp.code(secret, now - timedelta(seconds=30))
    await world.auth.login("alice", PASSWORD, code=late)  # clock drift: one step back
    await world.auth.login("alice", PASSWORD, code=world.totp.code(secret, now))
    with pytest.raises(AuthenticationError):  # not later than the last step used
        await world.auth.login("alice", PASSWORD, code=late)
    with pytest.raises(AuthenticationError):  # two steps back
        await world.auth.login(
            "alice", PASSWORD, code=world.totp.code(secret, now - timedelta(seconds=60))
        )


async def test_recovery_codes_work_once(world: World) -> None:
    user = await world.account("alice")
    _, codes = await enable_totp(world, user)
    await world.auth.login("alice", PASSWORD, recovery_code=codes[0].lower().replace("-", " "))
    with pytest.raises(AuthenticationError):
        await world.auth.login("alice", PASSWORD, recovery_code=codes[0])
    await world.auth.login("alice", PASSWORD, recovery_code=codes[1])


async def test_wrong_codes_count_as_failures(world: World) -> None:
    user = await world.account("alice")
    await enable_totp(world, user)
    for _ in range(6):
        with pytest.raises(AuthenticationError):
            await world.auth.login("alice", PASSWORD, code="000000")
    with pytest.raises(TooManyAttemptsError):
        await world.auth.login("alice", PASSWORD, code="000000")


async def test_turning_totp_off_needs_a_code(world: World) -> None:
    user = await world.account("alice")
    secret, codes = await enable_totp(world, user)
    with pytest.raises(PermissionDeniedError):
        await world.auth.disable_totp(user.id, "000000")
    renewed = await world.auth.renew_recovery_codes(user.id, codes[0])
    with pytest.raises(PermissionDeniedError):
        await world.auth.disable_totp(user.id, codes[1])  # replaced by the renewed ones
    await world.auth.disable_totp(user.id, renewed[0])
    await world.auth.login("alice", PASSWORD)
    with pytest.raises(ConflictError):
        await world.auth.disable_totp(user.id, world.totp.code(secret, world.clock.now()))


async def test_another_setup_replaces_an_unconfirmed_one(world: World) -> None:
    user = await world.account("alice")
    first = await world.auth.begin_totp(user.id)
    second = await world.auth.begin_totp(user.id)
    with pytest.raises(ValidationError):
        await world.auth.confirm_totp(user.id, world.totp.code(first.secret, world.clock.now()))
    await world.auth.confirm_totp(user.id, world.totp.code(second.secret, world.clock.now()))


# --- API tokens ---------------------------------------------------------------------------------


async def test_api_tokens(world: World) -> None:
    user = await world.account("alice")
    read, read_token = await world.auth.create_api_token(user.id, "cli", TokenScope.READ)
    world.clock.advance(timedelta(seconds=1))  # listed oldest first
    write, write_token = await world.auth.create_api_token(
        user.id, "mcp", TokenScope.READ_WRITE, expires_at=world.clock.now() + timedelta(days=1)
    )
    assert read_token.startswith("papiq_") and read.token_hash == hash_token(read_token)
    assert [t.id for t in await world.auth.list_api_tokens(user.id)] == [read.id, write.id]

    principal = await world.auth.authenticate_token(read_token)
    assert principal.id == user.id and not principal.can_write
    assert (await world.auth.authenticate_token(write_token)).can_write
    [stored, _] = await world.auth.list_api_tokens(user.id)
    assert stored.last_used_at == world.clock.now()

    world.clock.advance(timedelta(days=1))
    with pytest.raises(AuthenticationError):
        await world.auth.authenticate_token(write_token)  # expired
    await world.auth.revoke_api_token(user.id, read.id)
    with pytest.raises(AuthenticationError):
        await world.auth.authenticate_token(read_token)
    with pytest.raises(AuthenticationError):
        await world.auth.authenticate_token("papiq_made-up")


async def test_tokens_of_others_cannot_be_revoked(world: World) -> None:
    alice, bob = await world.account("alice"), await world.account("bob")
    token, _ = await world.auth.create_api_token(alice.id, "cli", TokenScope.READ)
    for id in (token.id, ApiTokenId(new_id())):
        with pytest.raises(NotFoundError):
            await world.auth.revoke_api_token(bob.id, id)


async def test_tokens_of_deactivated_users_are_refused(world: World) -> None:
    user = await world.account("alice")
    _, token = await world.auth.create_api_token(user.id, "cli", TokenScope.READ)
    await set_active(world, user, False)
    with pytest.raises(AuthenticationError):
        await world.auth.authenticate_token(token)
    await set_active(world, user, True)
    await world.auth.authenticate_token(token)


async def test_token_names_and_expiry_are_checked(world: World) -> None:
    user = await world.account("alice")
    with pytest.raises(ValidationError):
        await world.auth.create_api_token(user.id, " ", TokenScope.READ)
    with pytest.raises(ValidationError):
        await world.auth.create_api_token(
            user.id, "old", TokenScope.READ, expires_at=world.clock.now()
        )
