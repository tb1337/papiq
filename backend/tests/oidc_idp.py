"""A simulated OpenID Connect provider for tests, served through `httpx2.MockTransport`: no
network. It checks what a real provider checks (client authentication, redirect URI, PKCE,
single-use codes) and can be told to misbehave (wrong issuer, audience, nonce, expiry, key)."""

import base64
import hashlib
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx2
from joserfc import jwt
from joserfc.jwk import KeySet, RSAKey

ISSUER = "https://idp.example"
CLIENT_ID = "papiq"
CLIENT_SECRET = "idp-client-secret"
REDIRECT_URI = "https://papiq.example/api/v1/auth/oidc/callback"

_KEYS: dict[str, RSAKey] = {}


def key(kid: str) -> RSAKey:
    """RSA keys are slow to make; one per id for the whole test run."""
    if kid not in _KEYS:
        _KEYS[kid] = RSAKey.generate_key(2048, parameters={"kid": kid, "use": "sig"})
    return _KEYS[kid]


@dataclass
class Grant:
    challenge: str
    nonce: str | None
    redirect_uri: str
    subject: str
    claims: dict[str, Any]


@dataclass
class SimulatedIdp:
    issuer: str = ISSUER
    # What the discovery document says; another value tests the issuer check.
    announced_issuer: str | None = None
    signing_key: str = "key-1"
    published_keys: list[str] = field(default_factory=lambda: ["key-1"])
    algorithms: list[str] = field(default_factory=lambda: ["RS256"])
    # Overrides of ID token claims (None removes a claim) for the next tokens.
    claim_overrides: dict[str, Any] = field(default_factory=dict)
    header_overrides: dict[str, Any] = field(default_factory=dict)
    unreachable: bool = False
    grants: dict[str, Grant] = field(default_factory=dict)
    requests: list[str] = field(default_factory=list)

    @property
    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self.handle)

    def consent(self, url: str, subject: str, **claims: Any) -> str:
        """The user signs in at the provider; returns the code for the callback."""
        query = {key: values[0] for key, values in parse_qs(urlsplit(url).query).items()}
        assert url.startswith(f"{self.issuer}/authorize?")
        assert query["response_type"] == "code"
        assert query["client_id"] == CLIENT_ID
        assert "openid" in query["scope"].split()
        assert query["code_challenge_method"] == "S256"
        assert len(query["state"]) >= 32
        code = f"code-{len(self.grants) + 1}"
        self.grants[code] = Grant(
            challenge=query["code_challenge"],
            nonce=query.get("nonce"),
            redirect_uri=query["redirect_uri"],
            subject=subject,
            claims=claims,
        )
        return code

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(f"{request.method} {request.url.path}")
        if self.unreachable:
            raise httpx2.ConnectError("unreachable", request=request)
        path = request.url.path
        if path == "/.well-known/openid-configuration":
            return httpx2.Response(200, json=self.discovery())
        if path == "/jwks":
            keys = KeySet([key(kid) for kid in self.published_keys])
            return httpx2.Response(200, json=keys.as_dict(private=False))
        if path == "/token" and request.method == "POST":
            return self.token(request)
        return httpx2.Response(404)

    def discovery(self) -> dict[str, Any]:
        return {
            "issuer": self.announced_issuer or self.issuer,
            "authorization_endpoint": f"{self.issuer}/authorize",
            "token_endpoint": f"{self.issuer}/token",
            "jwks_uri": f"{self.issuer}/jwks",
            "response_types_supported": ["code"],
            "subject_types_supported": ["public"],
            "id_token_signing_alg_values_supported": self.algorithms,
            "code_challenge_methods_supported": ["S256"],
        }

    def token(self, request: httpx2.Request) -> httpx2.Response:
        expected = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
        if request.headers.get("authorization") != f"Basic {expected}":
            return _error(401, "invalid_client")
        form = {key: values[0] for key, values in parse_qs(request.content.decode()).items()}
        grant = self.grants.pop(form.get("code", ""), None)
        if form.get("grant_type") != "authorization_code" or grant is None:
            return _error(400, "invalid_grant")
        if form.get("redirect_uri") != grant.redirect_uri:
            return _error(400, "invalid_grant")
        verifier = form.get("code_verifier", "")
        digest = hashlib.sha256(verifier.encode()).digest()
        if base64.urlsafe_b64encode(digest).decode().rstrip("=") != grant.challenge:
            return _error(400, "invalid_grant")
        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": self.issuer,
            "sub": grant.subject,
            "aud": CLIENT_ID,
            "iat": now,
            "exp": now + 300,
            **({"nonce": grant.nonce} if grant.nonce else {}),
            **grant.claims,
        }
        for name, value in self.claim_overrides.items():
            if value is None:
                claims.pop(name, None)
            else:
                claims[name] = value
        header = {"alg": "RS256", "kid": self.signing_key, **self.header_overrides}
        id_token = jwt.encode(header, claims, key(self.signing_key), algorithms=[header["alg"]])
        return httpx2.Response(
            200,
            json={
                "access_token": "access",
                "token_type": "Bearer",
                "expires_in": 300,
                "id_token": id_token,
            },
        )


def _error(status: int, error: str) -> httpx2.Response:
    return httpx2.Response(status, json={"error": error})
