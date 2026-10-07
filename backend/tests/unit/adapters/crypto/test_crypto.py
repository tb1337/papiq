"""Argon2id, AES-256-GCM and pyotp pass the contract suites; TOTP matches RFC 6238."""

import base64
from datetime import UTC, datetime

import pytest

from papiq.adapters.outbound.crypto import (
    AesGcmCipher,
    Argon2PasswordHasher,
    PyotpTotp,
    decode_key,
)
from papiq.core.ports import DecryptionError
from tests.contracts.identity import PasswordHasherContract, SecretCipherContract, TotpContract

# Low cost, so the tests stay fast; production uses the RFC 9106 low-memory profile.
FAST = {"time_cost": 1, "memory_cost": 8, "parallelism": 1}


@pytest.fixture
def password_hasher() -> Argon2PasswordHasher:
    return Argon2PasswordHasher(**FAST)


@pytest.fixture
def cipher() -> AesGcmCipher:
    return AesGcmCipher(bytes(range(32)))


@pytest.fixture
def totp() -> PyotpTotp:
    return PyotpTotp()


class TestArgon2PasswordHasher(PasswordHasherContract):
    pass


class TestAesGcmCipher(SecretCipherContract):
    pass


class TestPyotpTotp(TotpContract):
    pass


async def test_argon2id_with_rfc_9106_defaults() -> None:
    hashed = await Argon2PasswordHasher().hash("correct horse battery")
    assert hashed.startswith("$argon2id$v=19$m=65536,t=3,p=4$")


async def test_hashes_with_other_parameters_need_a_rehash() -> None:
    old = await Argon2PasswordHasher(**FAST).hash("correct horse battery")
    current = Argon2PasswordHasher(time_cost=2, memory_cost=16, parallelism=1)
    assert await current.verify(old, "correct horse battery")
    assert current.needs_rehash(old)


def test_another_key_cannot_decrypt() -> None:
    ciphertext = AesGcmCipher(bytes(32)).encrypt(b"secret", context=b"u")
    with pytest.raises(DecryptionError):
        AesGcmCipher(bytes(range(32))).decrypt(ciphertext, context=b"u")
    with pytest.raises(DecryptionError):
        AesGcmCipher(bytes(32)).decrypt(b"\x02" + ciphertext[1:], context=b"u")


def test_every_encryption_uses_a_new_nonce() -> None:
    cipher = AesGcmCipher(bytes(32))
    assert cipher.encrypt(b"x", context=b"u") != cipher.encrypt(b"x", context=b"u")


def test_keys_are_32_bytes_of_base64() -> None:
    key = bytes(range(32))
    assert decode_key(base64.b64encode(key).decode()) == key
    assert decode_key(base64.urlsafe_b64encode(key).decode().rstrip("=")) == key
    for bad in ("", "not base64!", base64.b64encode(bytes(16)).decode()):
        with pytest.raises(ValueError):
            decode_key(bad)


# RFC 6238, appendix B, SHA-1: the secret "12345678901234567890"; 8-digit codes truncated to
# their last 6 digits are the 6-digit codes.
RFC_SECRET = base64.b32encode(b"12345678901234567890").decode()
RFC_VECTORS = [
    (59, "287082"),
    (1111111109, "081804"),
    (1111111111, "050471"),
    (1234567890, "005924"),
    (2000000000, "279037"),
]


@pytest.mark.parametrize(("seconds", "code"), RFC_VECTORS)
def test_rfc_6238_vectors(seconds: int, code: str) -> None:
    at = datetime.fromtimestamp(seconds, UTC)
    totp = PyotpTotp()
    assert totp.code(RFC_SECRET, at) == code
    assert totp.matching_step(RFC_SECRET, code, at) == seconds // 30
