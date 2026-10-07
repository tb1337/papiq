"""TOTP (RFC 6238) with pyotp: SHA-1, 6 digits, 30-second steps."""

import hmac
from datetime import datetime

import pyotp

_STEP = 30


class PyotpTotp:
    def new_secret(self) -> str:
        return pyotp.random_base32()

    def code(self, secret: str, at: datetime) -> str:
        return pyotp.TOTP(secret).at(at)

    def matching_step(self, secret: str, code: str, at: datetime) -> int | None:
        code = code.strip()
        if not (len(code) == 6 and code.isascii() and code.isdigit()):
            return None
        totp = pyotp.TOTP(secret)
        now = int(at.timestamp()) // _STEP
        for step in (now - 1, now, now + 1):
            if hmac.compare_digest(totp.at(step * _STEP), code):
                return step
        return None

    def provisioning_uri(self, secret: str, *, account: str, issuer: str) -> str:
        return pyotp.TOTP(secret).provisioning_uri(name=account, issuer_name=issuer)
