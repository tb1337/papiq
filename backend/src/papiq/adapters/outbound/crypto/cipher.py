"""AES-256-GCM for secrets at rest.

Ciphertext: one version byte (1), a random 96-bit nonce, then the encrypted data with its tag.
The context (e.g. the user id) is authenticated as associated data.
"""

import base64
import binascii
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from papiq.core.ports import DecryptionError

KEY_BYTES = 32
_VERSION = b"\x01"
_NONCE_BYTES = 12


def decode_key(text: str) -> bytes:
    """A key in base64 (standard or URL-safe, padding optional) of exactly 32 bytes, e.g.
    from `openssl rand -base64 32`. Raises ValueError."""
    cleaned = text.strip().replace("-", "+").replace("_", "/")
    cleaned += "=" * (-len(cleaned) % 4)
    try:
        key = base64.b64decode(cleaned, validate=True)
    except binascii.Error:
        raise ValueError("not base64") from None
    if len(key) != KEY_BYTES:
        raise ValueError(f"must decode to {KEY_BYTES} bytes, not {len(key)}")
    return key


class AesGcmCipher:
    def __init__(self, key: bytes) -> None:
        if len(key) != KEY_BYTES:
            raise ValueError(f"the key must have {KEY_BYTES} bytes")
        self._aead = AESGCM(key)

    def encrypt(self, plaintext: bytes, *, context: bytes) -> bytes:
        nonce = os.urandom(_NONCE_BYTES)
        return _VERSION + nonce + self._aead.encrypt(nonce, plaintext, context)

    def decrypt(self, ciphertext: bytes, *, context: bytes) -> bytes:
        if not ciphertext.startswith(_VERSION) or len(ciphertext) < 1 + _NONCE_BYTES:
            raise DecryptionError("unknown ciphertext format")
        nonce, data = ciphertext[1 : 1 + _NONCE_BYTES], ciphertext[1 + _NONCE_BYTES :]
        try:
            return self._aead.decrypt(nonce, data, context)
        except InvalidTag:
            raise DecryptionError("the ciphertext does not decrypt with this key") from None
