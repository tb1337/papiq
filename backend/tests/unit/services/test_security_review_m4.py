"""Failing tests from the M4 security review (`.idea/reviews/M4-security.md`), service level.

Each test encodes the behaviour the review recommends and fails on the reviewed branch on
purpose; the finding it belongs to is named in its docstring.
"""

import asyncio
import logging

import pytest

from papiq.composition.errors import ConfigurationError
from papiq.composition.settings import Settings
from papiq.core.domain.errors import AuthenticationError, TooManyAttemptsError
from papiq.core.services.auth import AuthService
from tests.builders import PASSWORD
from tests.unit.services.conftest import World

SOURCE = "203.0.113.1"
DEV_KEY = "ZGV2LWtleS1kZXYta2V5LWRldi1rZXktZGV2LWtleS0="


class GatedHasher:
    """Verifies only once `release` is set, so that many sign-ins can be in flight at once;
    counts how many callers wait."""

    def __init__(self, inner: object) -> None:
        self._inner = inner
        self.release = asyncio.Event()
        self.waiting = 0

    async def hash(self, password: str) -> str:
        return await self._inner.hash(password)  # type: ignore[attr-defined, no-any-return]

    async def verify(self, hash: str, password: str) -> bool:
        self.waiting += 1
        await self.release.wait()
        return await self._inner.verify(hash, password)  # type: ignore[attr-defined, no-any-return]

    def needs_rehash(self, hash: str) -> bool:
        return self._inner.needs_rehash(hash)  # type: ignore[attr-defined, no-any-return]


async def _burst(world: World, attempts: int) -> tuple[list[object], GatedHasher]:
    gate = GatedHasher(world.hasher)
    auth = AuthService(world.uow, world.clock, hasher=gate, cipher=world.cipher, totp=world.totp)
    tasks = [
        asyncio.create_task(auth.login("alice", "wrong password!", source=SOURCE))
        for _ in range(attempts)
    ]
    # Let every attempt pass the throttle check and reach the (blocked) hash.
    while gate.waiting < attempts and not all(task.done() for task in tasks):
        await asyncio.sleep(0)
    gate.release.set()
    return list(await asyncio.gather(*tasks, return_exceptions=True)), gate


# --- M4-01: a shared source address locks everyone out ------------------------------------------


def test_production_settings_need_the_trusted_proxies() -> None:
    """M4-01: Papiq speaks plain HTTP; with `Secure` cookies (default) a TLS-terminating proxy
    is in front of it. Without `PAPIQ_FORWARDED_ALLOW_IPS` every request then carries the
    proxy's address, and the per-source throttle becomes a lock for the whole instance. Such
    a configuration should not start (or at least warn loudly)."""
    with pytest.raises(ConfigurationError, match="FORWARDED_ALLOW_IPS"):
        Settings(secret_key=DEV_KEY, cookie_secure=True, forwarded_allow_ips=None)  # type: ignore[arg-type]


async def test_failures_from_one_address_do_not_refuse_other_accounts(world: World) -> None:
    """M4-01 (recommended behaviour, contradicts `test_failures_per_source_block_the_source`):
    thirty wrong guesses from one address must not refuse a correct sign-in to an account
    without failures. Behind a proxy or NAT the address is shared by everyone."""
    await world.account("alice")
    for number in range(30):
        with pytest.raises(AuthenticationError):
            await world.auth.login(f"guess-{number}", "wrong password!", source=SOURCE)
    await world.auth.login("alice", PASSWORD, source=SOURCE)


# --- M4-02: the throttle is not atomic ---------------------------------------------------------


async def test_a_concurrent_burst_does_not_bypass_the_throttle(world: World) -> None:
    """M4-02: the throttle is checked before the hash and counted after it. Attempts that
    arrive together all pass the check. Of 60 concurrent attempts against one account from
    one address, at least 60 - 30 (source cap) must be refused."""
    await world.account("alice")
    attempts = 60
    results, _ = await _burst(world, attempts)
    refused = sum(isinstance(result, TooManyAttemptsError) for result in results)
    assert refused >= attempts - 30, f"{refused} of {attempts} concurrent attempts were refused"


# --- M4-07: the typed username is logged -------------------------------------------------------


async def test_failed_sign_ins_do_not_log_the_typed_username(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    """M4-07: `sign-in failed` logs the account key, i.e. the username as typed. A password
    typed into the username field (a common slip) ends up in the log."""
    typed_into_the_wrong_field = "my secret passphrase 42"
    with (
        caplog.at_level(logging.INFO, logger="papiq.core.services.auth"),
        pytest.raises(AuthenticationError),
    ):
        await world.auth.login(typed_into_the_wrong_field, "x", source=SOURCE)
    for record in caplog.records:
        logged = record.getMessage() + repr(record.__dict__.get("keys"))
        assert typed_into_the_wrong_field.casefold() not in logged.casefold(), logged
