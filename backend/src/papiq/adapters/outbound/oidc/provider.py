"""OpenID Connect provider through Authlib.

- Endpoints and keys come from the provider's discovery document
  (`<issuer>/.well-known/openid-configuration`), fetched once; its `issuer` must equal the
  configured one exactly. The keys (JWKS) are fetched again once when a token names an unknown
  key, so the provider can rotate them.
- The ID token is checked here, completely: signature with an asymmetric algorithm the provider
  announces (never `none` or HMAC), `iss`, `aud` (contains the client id; with several
  audiences `azp` must be the client id), `exp` and `iat` with one minute of leeway, and the
  nonce of this flow.
- Nothing of tokens or codes is logged; failures are logged by kind only.
"""

import asyncio
import hmac
import logging
import time
from collections.abc import Callable, Collection
from typing import Any, TypeGuard

import httpx2
from authlib.common.errors import AuthlibBaseError
from authlib.integrations.httpx_client import AsyncOAuth2Client
from joserfc import jwt
from joserfc.errors import InvalidKeyIdError, JoseError, MissingKeyError
from joserfc.jwk import KeySet

from papiq.core.domain.errors import AuthenticationError, IdentityProviderError
from papiq.core.domain.identity import OidcIdentity

log = logging.getLogger(__name__)

# Asymmetric signature algorithms accepted for ID tokens.
SIGNATURE_ALGORITHMS = frozenset(
    {"RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512", "EdDSA"}
)
LEEWAY = 60  # seconds
TIMEOUT = 10.0  # seconds per request to the provider


class AuthlibOidcProvider:
    def __init__(
        self,
        *,
        issuer: str,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        scopes: Collection[str] = ("openid", "profile", "email"),
        username_claim: str = "preferred_username",
        transport: httpx2.AsyncBaseTransport | None = None,
        now: Callable[[], float] = time.time,
    ) -> None:
        """`transport` replaces the network in tests; `now` the clock for token times."""
        self._issuer = issuer
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._scope = " ".join(scopes)
        self._username_claim = username_claim
        self._transport = transport
        self._now = now
        self._metadata: dict[str, Any] | None = None
        self._keys: KeySet | None = None
        self._lock = asyncio.Lock()

    @property
    def issuer(self) -> str:
        return self._issuer

    async def authorization_url(self, *, state: str, nonce: str, code_verifier: str) -> str:
        metadata = await self._discover()
        async with self._client() as client:
            url, _ = client.create_authorization_url(
                metadata["authorization_endpoint"],
                state=state,
                code_verifier=code_verifier,
                nonce=nonce,
            )
        return str(url)

    async def authenticate(self, *, code: str, code_verifier: str, nonce: str) -> OidcIdentity:
        metadata = await self._discover()
        try:
            async with self._client() as client:
                token = await client.fetch_token(
                    metadata["token_endpoint"],
                    grant_type="authorization_code",
                    code=code,
                    code_verifier=code_verifier,
                )
        except AuthlibBaseError as error:
            log.warning(
                "identity provider refused the code", extra={"error": str(error.error)[:100]}
            )
            raise AuthenticationError("the identity provider refused the sign-in") from None
        except httpx2.HTTPError as error:
            log.warning("identity provider unreachable", extra={"error": type(error).__name__})
            raise IdentityProviderError("the identity provider cannot be reached") from None
        id_token = token.get("id_token")
        if not isinstance(id_token, str):
            log.warning("identity provider sent no ID token")
            raise AuthenticationError("the identity provider sent no ID token")
        claims = await self._verify(id_token, metadata)
        self._check_claims(claims, nonce)
        return OidcIdentity(
            issuer=claims["iss"],
            subject=claims["sub"],
            username=_text(claims.get(self._username_claim)),
            email=_text(claims.get("email")),
            name=_text(claims.get("name")),
        )

    # --- ID token -------------------------------------------------------------------------------

    async def _verify(self, id_token: str, metadata: dict[str, Any]) -> dict[str, Any]:
        announced = metadata.get("id_token_signing_alg_values_supported") or ["RS256"]
        algorithms = sorted(SIGNATURE_ALGORITHMS & set(announced))
        if not algorithms:
            raise IdentityProviderError("the identity provider signs with no accepted algorithm")
        for attempt in range(2):
            keys = await self._key_set(refresh=attempt > 0)
            try:
                return dict(jwt.decode(id_token, keys, algorithms=algorithms).claims)
            except JoseError as error:
                # An unknown key id: the provider may have rotated its keys; fetch them again.
                if attempt == 0 and _unknown_key(error):
                    continue
                log.warning("invalid ID token", extra={"error": type(error).__name__})
                raise AuthenticationError("the ID token is not valid") from None
            except ValueError:
                log.warning("malformed ID token")
                raise AuthenticationError("the ID token is not valid") from None
        raise AuthenticationError("the ID token is not valid")

    def _check_claims(self, claims: dict[str, Any], nonce: str) -> None:
        now = self._now()
        audience = claims.get("aud")
        audiences = [audience] if isinstance(audience, str) else audience
        problems = []
        if claims.get("iss") != self._issuer:
            problems.append("iss")
        if not isinstance(claims.get("sub"), str) or not claims["sub"]:
            problems.append("sub")
        if not isinstance(audiences, list) or self._client_id not in audiences:
            problems.append("aud")
        elif len(audiences) > 1 and claims.get("azp") != self._client_id:
            problems.append("azp")
        if "azp" in claims and claims["azp"] != self._client_id:
            problems.append("azp")
        expires, issued = claims.get("exp"), claims.get("iat")
        if not _number(expires) or expires + LEEWAY <= now:
            problems.append("exp")
        if not _number(issued) or issued - LEEWAY > now:
            problems.append("iat")
        token_nonce = claims.get("nonce")
        if not isinstance(token_nonce, str) or not hmac.compare_digest(token_nonce, nonce):
            problems.append("nonce")
        if problems:
            log.warning("ID token rejected", extra={"claims": sorted(set(problems))})
            raise AuthenticationError("the ID token is not valid")

    # --- discovery and keys ---------------------------------------------------------------------

    async def _discover(self) -> dict[str, Any]:
        async with self._lock:
            if self._metadata is None:
                url = self._issuer.rstrip("/") + "/.well-known/openid-configuration"
                metadata = await self._get_json(url)
                if metadata.get("issuer") != self._issuer:
                    log.error("discovery names another issuer")
                    raise IdentityProviderError("the identity provider names another issuer")
                for field in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
                    if not isinstance(metadata.get(field), str):
                        raise IdentityProviderError(f"the discovery document has no {field}")
                self._metadata = metadata
            return self._metadata

    async def _key_set(self, *, refresh: bool = False) -> KeySet:
        metadata = await self._discover()
        async with self._lock:
            if self._keys is None or refresh:
                document = await self._get_json(metadata["jwks_uri"])
                try:
                    self._keys = KeySet.import_key_set(document)  # type: ignore[arg-type]
                except (JoseError, ValueError, KeyError, TypeError):
                    raise IdentityProviderError(
                        "the identity provider's keys are invalid"
                    ) from None
            return self._keys

    async def _get_json(self, url: str) -> dict[str, Any]:
        try:
            async with httpx2.AsyncClient(transport=self._transport, timeout=TIMEOUT) as client:
                response = await client.get(url, headers={"Accept": "application/json"})
                response.raise_for_status()
                document = response.json()
        except (httpx2.HTTPError, ValueError) as error:
            log.warning(
                "identity provider unreachable", extra={"url": url, "error": type(error).__name__}
            )
            raise IdentityProviderError("the identity provider cannot be reached") from None
        if not isinstance(document, dict):
            raise IdentityProviderError("the identity provider answered unusably")
        return document

    def _client(self) -> AsyncOAuth2Client:
        return AsyncOAuth2Client(
            client_id=self._client_id,
            client_secret=self._client_secret,
            scope=self._scope,
            redirect_uri=self._redirect_uri,
            code_challenge_method="S256",
            transport=self._transport,
            timeout=TIMEOUT,
        )


def _unknown_key(error: JoseError) -> bool:
    return isinstance(error, InvalidKeyIdError | MissingKeyError)


def _number(value: object) -> TypeGuard[int | float]:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None
