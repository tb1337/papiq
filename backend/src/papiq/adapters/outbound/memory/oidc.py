"""A stand-in identity provider for tests: the test decides who signs in."""

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlsplit

from papiq.core.domain.errors import AuthenticationError
from papiq.core.domain.identity import OidcIdentity


@dataclass(frozen=True)
class _Grant:
    challenge: str
    nonce: str
    identity: OidcIdentity


class FakeOidcProvider:
    """`authorization_url` points to an imaginary provider; `consent` plays the user signing in
    there and returns the code the browser would bring back."""

    def __init__(self, issuer: str = "https://idp.test") -> None:
        self._issuer = issuer
        self._grants: dict[str, _Grant] = {}

    @property
    def issuer(self) -> str:
        return self._issuer

    async def authorization_url(self, *, state: str, nonce: str, code_verifier: str) -> str:
        query = {
            "response_type": "code",
            "client_id": "papiq",
            "scope": "openid profile email",
            "state": state,
            "nonce": nonce,
            "code_challenge": _challenge(code_verifier),
            "code_challenge_method": "S256",
        }
        return f"{self._issuer}/authorize?{urlencode(query)}"

    def consent(self, url: str, subject: str, *, username: str | None = None) -> str:
        query = {key: values[0] for key, values in parse_qs(urlsplit(url).query).items()}
        code = secrets.token_urlsafe(16)
        self._grants[code] = _Grant(
            challenge=query["code_challenge"],
            nonce=query["nonce"],
            identity=OidcIdentity(issuer=self._issuer, subject=subject, username=username),
        )
        return code

    async def authenticate(self, *, code: str, code_verifier: str, nonce: str) -> OidcIdentity:
        grant = self._grants.pop(code, None)
        if (
            grant is None
            or not hmac.compare_digest(grant.challenge, _challenge(code_verifier))
            or not hmac.compare_digest(grant.nonce, nonce)
        ):
            raise AuthenticationError("the identity provider refused the sign-in")
        return grant.identity


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")
