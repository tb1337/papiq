"""Contract suites for identity: the repositories in the unit of work and the cryptography
ports."""

import asyncio
import base64
import hashlib
from collections.abc import Callable
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest

from papiq.core.domain.errors import AuthenticationError, ConcurrencyError, ConflictError
from papiq.core.domain.identity import (
    ACCOUNT_THROTTLE,
    ApiToken,
    Credential,
    ExternalIdentity,
    LoginFailures,
    LoginMethod,
    Session,
    ThrottleRule,
    TokenScope,
    TotpSetting,
    hash_token,
)
from papiq.core.domain.users import User
from papiq.core.ports import (
    DecryptionError,
    OidcProvider,
    PasswordHasher,
    SecretCipher,
    Totp,
    UnitOfWorkFactory,
)
from tests import builders
from tests.builders import NOW
from tests.contracts.unit_of_work import seed

HOUR = timedelta(hours=1)


async def a_user(uow_factory: UnitOfWorkFactory) -> User:
    user = builders.user()
    await seed(uow_factory, user)
    return user


def a_session(user: User, *, at: timedelta = timedelta(0)) -> Session:
    session, _ = Session.start(
        user_id=user.id, method=LoginMethod.PASSWORD, now=NOW + at, max_age=24 * HOUR
    )
    return session


class IdentityRepositoriesContract:
    # --- credentials ----------------------------------------------------------------------------

    async def test_credentials_round_trip_with_totp_and_recovery_codes(
        self, uow_factory: UnitOfWorkFactory
    ) -> None:
        user = await a_user(uow_factory)
        credential = Credential(
            user_id=user.id,
            password_hash="$argon2id$v=19$...",
            password_changed_at=NOW,
            totp=TotpSetting(secret=b"\x00\x01encrypted", confirmed=True, last_step=2**40),
            recovery_codes={hash_token("A"), hash_token("B")},
        )
        async with uow_factory() as uow:
            await uow.credentials.add(credential)
            await uow.commit()
        async with uow_factory() as uow:
            assert await uow.credentials.get(user.id) == credential

    async def test_credentials_update_checks_the_version(
        self, uow_factory: UnitOfWorkFactory
    ) -> None:
        user = await a_user(uow_factory)
        async with uow_factory() as uow:
            await uow.credentials.add(Credential(user_id=user.id, password_hash="one"))
            await uow.commit()
        async with uow_factory() as uow:
            first = await uow.credentials.get(user.id)
        async with uow_factory() as uow:
            second = await uow.credentials.get(user.id)
            second.password_hash = "two"
            second.recovery_codes = {hash_token("X")}
            await uow.credentials.update(second)
            await uow.commit()
        assert second.version == first.version + 1
        first.password_hash = "stale"
        with pytest.raises(ConcurrencyError):
            async with uow_factory() as uow:
                await uow.credentials.update(first)
                await uow.commit()
        async with uow_factory() as uow:
            stored = await uow.credentials.get(user.id)
            assert stored.password_hash == "two"
            assert stored.recovery_codes == {hash_token("X")}
            await uow.credentials.remove(user.id)
            await uow.commit()
        async with uow_factory() as uow:
            assert await uow.credentials.find(user.id) is None

    # --- sessions -------------------------------------------------------------------------------

    async def test_sessions_are_found_by_token_and_touched(
        self, uow_factory: UnitOfWorkFactory
    ) -> None:
        user = await a_user(uow_factory)
        session = a_session(user)
        async with uow_factory() as uow:
            await uow.sessions.add(session)
            await uow.commit()
        async with uow_factory() as uow:
            assert await uow.sessions.find_by_token(session.token_hash) == session
            assert await uow.sessions.find_by_token(hash_token("other")) is None
            await uow.sessions.touch(session.id, NOW + HOUR)
            await uow.sessions.touch(session.id, NOW)  # never backwards
            await uow.commit()
        async with uow_factory() as uow:
            found = await uow.sessions.find_by_token(session.token_hash)
            assert found is not None
            assert found.last_seen_at == NOW + HOUR
            assert found.version == session.version  # touching is no change

    async def test_concurrent_touches_do_not_conflict(self, uow_factory: UnitOfWorkFactory) -> None:
        user = await a_user(uow_factory)
        session = a_session(user)
        async with uow_factory() as uow:
            await uow.sessions.add(session)
            await uow.commit()
        async with uow_factory() as first:
            await first.sessions.find_by_token(session.token_hash)
            async with uow_factory() as second:
                await second.sessions.touch(session.id, NOW + HOUR)
                await second.commit()
            await first.sessions.touch(session.id, NOW + 2 * HOUR)
            await first.commit()

    async def test_session_tokens_are_unique(self, uow_factory: UnitOfWorkFactory) -> None:
        user = await a_user(uow_factory)
        session = a_session(user)
        twin = a_session(user)
        twin.token_hash = session.token_hash
        with pytest.raises(ConflictError):
            async with uow_factory() as uow:
                await uow.sessions.add(session)
                await uow.sessions.add(twin)
                await uow.commit()

    async def test_sessions_are_removed_per_user_and_purged(
        self, uow_factory: UnitOfWorkFactory
    ) -> None:
        user, other = await a_user(uow_factory), await a_user(uow_factory)
        keep, gone, others = a_session(user), a_session(user), a_session(other)
        old = a_session(other, at=-30 * HOUR)  # expired
        idle = a_session(other, at=-2 * HOUR)
        async with uow_factory() as uow:
            for session in (keep, gone, others, old, idle):
                await uow.sessions.add(session)
            await uow.commit()
        async with uow_factory() as uow:
            assert await uow.sessions.remove_for_user(user.id, keep=keep.id) == 1
            assert await uow.sessions.purge(now=NOW, idle_before=NOW - HOUR) == 2
            await uow.sessions.remove(others.id)
            await uow.sessions.remove(others.id)  # missing: no-op
            await uow.commit()
        async with uow_factory() as uow:
            remaining = [
                await uow.sessions.find_by_token(s.token_hash)
                for s in (keep, gone, others, old, idle)
            ]
            assert [s.id if s else None for s in remaining] == [keep.id, None, None, None, None]

    # --- API tokens -----------------------------------------------------------------------------

    async def test_api_tokens(self, uow_factory: UnitOfWorkFactory) -> None:
        user, other = await a_user(uow_factory), await a_user(uow_factory)
        first, _ = ApiToken.issue(user_id=user.id, name="cli", scope=TokenScope.READ, now=NOW)
        second, _ = ApiToken.issue(
            user_id=user.id,
            name="mcp",
            scope=TokenScope.READ_WRITE,
            now=NOW + HOUR,
            expires_at=NOW + 48 * HOUR,
        )
        foreign, _ = ApiToken.issue(user_id=other.id, name="x", scope=TokenScope.READ, now=NOW)
        async with uow_factory() as uow:
            for token in (second, first, foreign):
                await uow.api_tokens.add(token)
            await uow.commit()
        async with uow_factory() as uow:
            assert await uow.api_tokens.list_for(user.id) == [first, second]
            assert await uow.api_tokens.find(second.id) == second
            assert await uow.api_tokens.find_by_token(foreign.token_hash) == foreign
            await uow.api_tokens.touch(first.id, NOW + HOUR)
            await uow.api_tokens.remove(second.id)
            await uow.commit()
        async with uow_factory() as uow:
            [stored] = await uow.api_tokens.list_for(user.id)
            assert stored.last_used_at == NOW + HOUR
            assert stored.version == first.version
            assert await uow.api_tokens.remove_for_user(other.id) == 1
            await uow.commit()
        async with uow_factory() as uow:
            assert await uow.api_tokens.find(foreign.id) is None

    # --- external identities --------------------------------------------------------------------

    async def test_external_identities_are_unique_per_issuer_and_subject(
        self, uow_factory: UnitOfWorkFactory
    ) -> None:
        user, other = await a_user(uow_factory), await a_user(uow_factory)
        link = ExternalIdentity.link(
            issuer="https://idp.example", subject="abc", user_id=user.id, now=NOW
        )
        elsewhere = ExternalIdentity.link(
            issuer="https://other.example", subject="abc", user_id=user.id, now=NOW + HOUR
        )
        async with uow_factory() as uow:
            await uow.external_identities.add(link)
            await uow.external_identities.add(elsewhere)
            await uow.commit()
        async with uow_factory() as uow:
            assert await uow.external_identities.find("https://idp.example", "abc") == link
            assert await uow.external_identities.find("https://idp.example", "ABC") is None
            assert await uow.external_identities.list_for(user.id) == [link, elsewhere]
        with pytest.raises(ConflictError):
            async with uow_factory() as uow:
                await uow.external_identities.add(
                    ExternalIdentity.link(
                        issuer="https://idp.example", subject="abc", user_id=other.id, now=NOW
                    )
                )
                await uow.commit()
        async with uow_factory() as uow:
            await uow.external_identities.remove(link.id)
            assert await uow.external_identities.remove_for_user(user.id) == 1
            await uow.commit()
        async with uow_factory() as uow:
            assert await uow.external_identities.list_for(user.id) == []

    # --- failed sign-ins ------------------------------------------------------------------------

    async def test_attempts_are_reserved_and_released(self, uow_factory: UnitOfWorkFactory) -> None:
        rule = ThrottleRule(free=2, window=HOUR, base=timedelta(minutes=1), limit=HOUR)

        async def reserve(at: timedelta = timedelta(0)) -> timedelta | None:
            async with uow_factory() as uow:
                wait = await uow.login_failures.reserve("account:alice", rule, NOW + at)
                await uow.commit()
            return wait

        async def stored() -> LoginFailures:
            async with uow_factory() as uow:
                found = await uow.login_failures.find("account:alice")
            assert found is not None
            return found

        assert await reserve() is None
        assert await reserve() is None
        assert (await stored()).blocked_until is None
        assert await reserve() is None  # the third: blocks for a minute as if it fails
        assert (await stored()).blocked_until == NOW + timedelta(minutes=1)
        assert await reserve(timedelta(seconds=20)) == timedelta(seconds=40)
        assert (await stored()).failures == 3  # a refused attempt is not counted
        async with uow_factory() as uow:
            await uow.login_failures.release("account:alice", rule)  # it did not fail
            await uow.commit()
        released = await stored()
        assert released.failures == 2 and released.blocked_until is None
        assert await reserve(timedelta(minutes=2)) is None
        assert (await stored()).blocked_until == NOW + timedelta(minutes=3)
        assert await reserve(2 * HOUR) is None  # the window has passed: counting starts anew
        fresh = await stored()
        assert fresh.failures == 1 and fresh.first_failure_at == NOW + 2 * HOUR
        assert fresh.blocked_until is None

    async def test_concurrent_reservations_are_all_counted(
        self, uow_factory: UnitOfWorkFactory
    ) -> None:
        rule = ThrottleRule(free=3, window=HOUR, base=HOUR, limit=HOUR)

        async def reserve() -> timedelta | None:
            async with uow_factory() as uow:
                wait = await uow.login_failures.reserve("source:192.0.2.1", rule, NOW)
                await uow.commit()
            return wait

        results = await asyncio.gather(*(reserve() for _ in range(10)))
        assert results.count(None) == 4  # three free ones, the fourth blocks
        async with uow_factory() as uow:
            found = await uow.login_failures.find("source:192.0.2.1")
        assert found is not None and found.failures == 4

    async def test_login_failures_are_removed_and_purged(
        self, uow_factory: UnitOfWorkFactory
    ) -> None:
        rule = ThrottleRule(free=0, window=HOUR, base=3 * HOUR, limit=3 * HOUR)
        async with uow_factory() as uow:
            await uow.login_failures.reserve("account:alice", ACCOUNT_THROTTLE, NOW)
            await uow.login_failures.reserve("account:bob", ACCOUNT_THROTTLE, NOW - 2 * HOUR)
            await uow.login_failures.reserve("source:192.0.2.1", rule, NOW - 2 * HOUR)
            await uow.commit()
        async with uow_factory() as uow:
            assert await uow.login_failures.purge(before=NOW - HOUR) == 1  # bob only
            await uow.login_failures.remove("account:alice")
            await uow.login_failures.release("account:nobody", rule)  # missing: no-op
            await uow.commit()
        async with uow_factory() as uow:
            assert await uow.login_failures.find("account:alice") is None
            assert await uow.login_failures.find("account:bob") is None
            assert await uow.login_failures.find("source:192.0.2.1") is not None  # blocked

    async def test_removing_a_user_with_identity_data(self, uow_factory: UnitOfWorkFactory) -> None:
        """The services remove identity data first; the repositories then remove the user."""
        user = await a_user(uow_factory)
        async with uow_factory() as uow:
            await uow.credentials.add(Credential(user_id=user.id, password_hash="h"))
            await uow.sessions.add(a_session(user))
            await uow.commit()
        async with uow_factory() as uow:
            await uow.sessions.remove_for_user(user.id)
            await uow.credentials.remove(user.id)
            await uow.users.remove(user.id)
            await uow.commit()
        async with uow_factory() as uow:
            assert await uow.users.find(user.id) is None


# --- cryptography -------------------------------------------------------------------------------


class PasswordHasherContract:
    async def test_verifies_the_right_password_only(self, password_hasher: PasswordHasher) -> None:
        hashed = await password_hasher.hash("correct horse battery")
        assert "correct horse battery" not in hashed
        assert await password_hasher.verify(hashed, "correct horse battery")
        assert not await password_hasher.verify(hashed, "correct horse batterY")
        assert not password_hasher.needs_rehash(hashed)

    async def test_hashes_differ_for_equal_passwords(self, password_hasher: PasswordHasher) -> None:
        first = await password_hasher.hash("correct horse battery")
        second = await password_hasher.hash("correct horse battery")
        assert await password_hasher.verify(second, "correct horse battery")
        assert first != second  # salted

    async def test_malformed_hashes_do_not_verify(self, password_hasher: PasswordHasher) -> None:
        assert not await password_hasher.verify("", "x")
        assert not await password_hasher.verify("not a hash", "x")
        assert password_hasher.needs_rehash("not a hash")


class SecretCipherContract:
    def test_round_trip(self, cipher: SecretCipher) -> None:
        ciphertext = cipher.encrypt(b"JBSWY3DPEHPK3PXP", context=b"user-1")
        assert b"JBSWY3DPEHPK3PXP" not in ciphertext
        assert cipher.decrypt(ciphertext, context=b"user-1") == b"JBSWY3DPEHPK3PXP"

    def test_other_context_or_tampering_fails(self, cipher: SecretCipher) -> None:
        ciphertext = cipher.encrypt(b"secret", context=b"user-1")
        with pytest.raises(DecryptionError):
            cipher.decrypt(ciphertext, context=b"user-2")
        tampered = ciphertext[:-1] + bytes([ciphertext[-1] ^ 1])
        with pytest.raises(DecryptionError):
            cipher.decrypt(tampered, context=b"user-1")


class TotpContract:
    def test_codes_match_their_step_and_neighbours(self, totp: Totp) -> None:
        secret = totp.new_secret()
        step = int(NOW.timestamp()) // 30
        assert totp.matching_step(secret, totp.code(secret, NOW), NOW) == step
        earlier = totp.code(secret, NOW - timedelta(seconds=30))
        later = totp.code(secret, NOW + timedelta(seconds=30))
        assert totp.matching_step(secret, earlier, NOW) == step - 1
        assert totp.matching_step(secret, later, NOW) == step + 1
        too_old = totp.code(secret, NOW - timedelta(seconds=90))
        assert totp.matching_step(secret, too_old, NOW) is None

    def test_wrong_codes_and_secrets(self, totp: Totp) -> None:
        secret, other = totp.new_secret(), totp.new_secret()
        assert secret != other
        code = totp.code(secret, NOW)
        assert len(code) == 6 and code.isdigit()
        assert totp.matching_step(other, code, NOW) is None
        assert totp.matching_step(secret, "abcdef", NOW) is None
        assert totp.matching_step(secret, "", NOW) is None

    def test_provisioning_uri(self, totp: Totp) -> None:
        secret = totp.new_secret()
        uri = totp.provisioning_uri(secret, account="alice", issuer="Papiq")
        assert uri.startswith("otpauth://totp/")
        assert secret in uri and "alice" in uri and "Papiq" in uri


# --- identity provider --------------------------------------------------------------------------

type Consent = Callable[[str, str, str | None], str]
"""Plays the user at the provider: (authorization URL, subject, username) -> code."""


def _query(url: str) -> dict[str, str]:
    return {key: values[0] for key, values in parse_qs(urlsplit(url).query).items()}


class OidcProviderContract:
    async def test_the_authorization_url_carries_state_nonce_and_pkce(
        self, oidc_provider: OidcProvider
    ) -> None:
        url = await oidc_provider.authorization_url(
            state="s" * 43, nonce="n" * 43, code_verifier="v" * 64
        )
        query = _query(url)
        assert query["state"] == "s" * 43 and query["nonce"] == "n" * 43
        assert query["response_type"] == "code"
        assert "openid" in query["scope"].split()
        assert query["code_challenge_method"] == "S256"
        digest = hashlib.sha256(b"v" * 64).digest()
        assert query["code_challenge"] == base64.urlsafe_b64encode(digest).decode().rstrip("=")
        assert "v" * 64 not in url

    async def test_a_code_gives_the_identity_once(
        self, oidc_provider: OidcProvider, oidc_consent: Consent
    ) -> None:
        url = await oidc_provider.authorization_url(
            state="s" * 43, nonce="n" * 43, code_verifier="v" * 64
        )
        code = oidc_consent(url, "subject-1", "alice")
        identity = await oidc_provider.authenticate(
            code=code, code_verifier="v" * 64, nonce="n" * 43
        )
        assert identity.issuer == oidc_provider.issuer
        assert identity.subject == "subject-1"
        assert identity.username == "alice"
        with pytest.raises(AuthenticationError):
            await oidc_provider.authenticate(code=code, code_verifier="v" * 64, nonce="n" * 43)

    async def test_the_verifier_and_the_nonce_must_match(
        self, oidc_provider: OidcProvider, oidc_consent: Consent
    ) -> None:
        url = await oidc_provider.authorization_url(
            state="s" * 43, nonce="n" * 43, code_verifier="v" * 64
        )
        code = oidc_consent(url, "subject-1", None)
        with pytest.raises(AuthenticationError):
            await oidc_provider.authenticate(code=code, code_verifier="w" * 64, nonce="n" * 43)
        url = await oidc_provider.authorization_url(
            state="s" * 43, nonce="n" * 43, code_verifier="v" * 64
        )
        code = oidc_consent(url, "subject-1", None)
        with pytest.raises(AuthenticationError):
            await oidc_provider.authenticate(code=code, code_verifier="v" * 64, nonce="m" * 43)
