"""Fast stand-ins for the cryptography ports, for tests. Not secure; never used in production."""

import hashlib
import hmac
import secrets
from datetime import datetime

from papiq.core.ports import DecryptionError

_STEP = 30


class FakePasswordHasher:
    """`fake$<round>$<salt>$<sha256>`. Hashes of an older `round` need a rehash."""

    def __init__(self, round: int = 1) -> None:
        self.round = round
        self.hashed = 0  # number of hashes made, for tests
        self.verified = 0

    async def hash(self, password: str) -> str:
        self.hashed += 1
        salt = secrets.token_hex(8)
        return f"fake${self.round}${salt}${_digest(salt, password)}"

    async def verify(self, hash: str, password: str) -> bool:
        self.verified += 1
        parts = hash.split("$")
        if len(parts) != 4 or parts[0] != "fake":
            return False
        return hmac.compare_digest(parts[3], _digest(parts[2], password))

    def needs_rehash(self, hash: str) -> bool:
        parts = hash.split("$")
        return len(parts) != 4 or parts[1] != str(self.round)


def _digest(salt: str, password: str) -> str:
    return hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()


class FakeCipher:
    """XOR with a key stream from the context and a MAC; good enough to tell contexts apart."""

    def __init__(self, key: bytes = b"fake key") -> None:
        self._key = key

    def encrypt(self, plaintext: bytes, *, context: bytes) -> bytes:
        body = bytes(
            a ^ b for a, b in zip(plaintext, self._stream(context, len(plaintext)), strict=True)
        )
        return self._mac(context, body) + body

    def decrypt(self, ciphertext: bytes, *, context: bytes) -> bytes:
        mac, body = ciphertext[:32], ciphertext[32:]
        if not hmac.compare_digest(mac, self._mac(context, body)):
            raise DecryptionError("the ciphertext does not decrypt")
        return bytes(a ^ b for a, b in zip(body, self._stream(context, len(body)), strict=True))

    def _stream(self, context: bytes, length: int) -> bytes:
        stream = b""
        counter = 0
        while len(stream) < length:
            stream += hashlib.sha256(self._key + context + counter.to_bytes(4)).digest()
            counter += 1
        return stream[:length]

    def _mac(self, context: bytes, body: bytes) -> bytes:
        return hmac.new(self._key, context + b"\0" + body, hashlib.sha256).digest()


class FakeTotp:
    """Codes are derived from secret and time step with SHA-256; same interface as RFC 6238."""

    def __init__(self) -> None:
        self._count = 0

    def new_secret(self) -> str:
        self._count += 1
        return f"FAKESECRET{self._count:06d}"

    def code(self, secret: str, at: datetime) -> str:
        return self._code(secret, int(at.timestamp()) // _STEP)

    def matching_step(self, secret: str, code: str, at: datetime) -> int | None:
        now = int(at.timestamp()) // _STEP
        for step in (now - 1, now, now + 1):
            if hmac.compare_digest(self._code(secret, step), code.strip()):
                return step
        return None

    def provisioning_uri(self, secret: str, *, account: str, issuer: str) -> str:
        return f"otpauth://totp/{issuer}:{account}?secret={secret}&issuer={issuer}"

    @staticmethod
    def _code(secret: str, step: int) -> str:
        digest = hashlib.sha256(f"{secret}:{step}".encode()).digest()
        return f"{int.from_bytes(digest[:4]) % 1_000_000:06d}"
