"""The Authlib adapter against a simulated provider: the contract, and every check of the ID
token. No network."""

import time
from typing import Any

import pytest

from papiq.adapters.outbound.oidc import AuthlibOidcProvider
from papiq.core.domain.errors import AuthenticationError, IdentityProviderError
from tests.contracts.identity import Consent, OidcProviderContract
from tests.oidc_idp import CLIENT_ID, CLIENT_SECRET, ISSUER, REDIRECT_URI, SimulatedIdp

VERIFIER = "v" * 64
NONCE = "n" * 43


@pytest.fixture
def idp() -> SimulatedIdp:
    return SimulatedIdp()


def provider_for(idp: SimulatedIdp, **options: Any) -> AuthlibOidcProvider:
    return AuthlibOidcProvider(
        issuer=ISSUER,
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        redirect_uri=REDIRECT_URI,
        transport=idp.transport,
        **options,
    )


@pytest.fixture
def oidc_provider(idp: SimulatedIdp) -> AuthlibOidcProvider:
    return provider_for(idp)


@pytest.fixture
def oidc_consent(idp: SimulatedIdp) -> Consent:
    def consent(url: str, subject: str, username: str | None) -> str:
        claims = {} if username is None else {"preferred_username": username}
        return idp.consent(url, subject, **claims)

    return consent


class TestAuthlibOidcProvider(OidcProviderContract):
    pass


async def sign_in(provider: AuthlibOidcProvider, idp: SimulatedIdp, **claims: Any) -> Any:
    url = await provider.authorization_url(state="s" * 43, nonce=NONCE, code_verifier=VERIFIER)
    code = idp.consent(url, "subject-1", **claims)
    return await provider.authenticate(code=code, code_verifier=VERIFIER, nonce=NONCE)


async def test_identity_with_claims(oidc_provider: AuthlibOidcProvider, idp: SimulatedIdp) -> None:
    identity = await sign_in(
        oidc_provider, idp, preferred_username="alice", email="a@example.org", name="Alice"
    )
    assert (identity.issuer, identity.subject) == (ISSUER, "subject-1")
    assert (identity.username, identity.email, identity.name) == ("alice", "a@example.org", "Alice")
    url = await oidc_provider.authorization_url(state="s" * 43, nonce=NONCE, code_verifier=VERIFIER)
    assert f"redirect_uri={REDIRECT_URI.replace(':', '%3A').replace('/', '%2F')}" in url


async def test_another_username_claim(idp: SimulatedIdp) -> None:
    provider = provider_for(idp, username_claim="email")
    identity = await sign_in(provider, idp, email="a@example.org")
    assert identity.username == "a@example.org"


async def test_discovery_and_keys_are_fetched_once(
    oidc_provider: AuthlibOidcProvider, idp: SimulatedIdp
) -> None:
    await sign_in(oidc_provider, idp)
    await sign_in(oidc_provider, idp)
    assert idp.requests.count("GET /.well-known/openid-configuration") == 1
    assert idp.requests.count("GET /jwks") == 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"iss": "https://evil.example"},
        {"aud": "another-client"},
        {"aud": [CLIENT_ID, "another-client"]},  # several audiences need azp
        {"aud": [CLIENT_ID, "another-client"], "azp": "another-client"},
        {"azp": "another-client"},
        {"exp": int(time.time()) - 120},
        {"iat": int(time.time()) + 600},
        {"exp": None},
        {"iat": None},
        {"nonce": None},
        {"nonce": "another nonce"},
        {"sub": None},
        {"sub": ""},
    ],
    ids=lambda overrides: ",".join(f"{k}={v}" for k, v in overrides.items()),
)
async def test_invalid_claims_are_rejected(
    oidc_provider: AuthlibOidcProvider, idp: SimulatedIdp, overrides: dict[str, Any]
) -> None:
    idp.claim_overrides = overrides
    with pytest.raises(AuthenticationError):
        await sign_in(oidc_provider, idp)


async def test_several_audiences_with_azp(
    oidc_provider: AuthlibOidcProvider, idp: SimulatedIdp
) -> None:
    idp.claim_overrides = {"aud": [CLIENT_ID, "another-client"], "azp": CLIENT_ID}
    await sign_in(oidc_provider, idp)


async def test_a_token_signed_with_an_unpublished_key_is_rejected(
    oidc_provider: AuthlibOidcProvider, idp: SimulatedIdp
) -> None:
    idp.signing_key = "attacker"
    with pytest.raises(AuthenticationError):
        await sign_in(oidc_provider, idp)
    idp.signing_key, idp.header_overrides = "attacker", {"kid": "key-1"}  # claims a known key
    with pytest.raises(AuthenticationError):
        await sign_in(oidc_provider, idp)


async def test_rotated_keys_are_fetched_again(
    oidc_provider: AuthlibOidcProvider, idp: SimulatedIdp
) -> None:
    await sign_in(oidc_provider, idp)
    idp.published_keys, idp.signing_key = ["key-1", "key-2"], "key-2"
    await sign_in(oidc_provider, idp)
    assert idp.requests.count("GET /jwks") == 2


async def test_only_announced_asymmetric_algorithms(idp: SimulatedIdp) -> None:
    idp.algorithms = ["HS256", "none"]
    with pytest.raises(IdentityProviderError):
        await sign_in(provider_for(idp), idp)
    idp.algorithms = ["RS256"]
    idp.header_overrides = {"alg": "RS512"}  # valid signature, algorithm not announced
    with pytest.raises(AuthenticationError):
        await sign_in(provider_for(idp), idp)


async def test_discovery_must_name_the_configured_issuer(idp: SimulatedIdp) -> None:
    idp.announced_issuer = "https://evil.example"
    with pytest.raises(IdentityProviderError):
        await provider_for(idp).authorization_url(state="s", nonce="n", code_verifier=VERIFIER)


async def test_an_unreachable_provider(idp: SimulatedIdp) -> None:
    idp.unreachable = True
    with pytest.raises(IdentityProviderError):
        await provider_for(idp).authorization_url(state="s", nonce="n", code_verifier=VERIFIER)


async def test_a_refused_code(oidc_provider: AuthlibOidcProvider, idp: SimulatedIdp) -> None:
    await oidc_provider.authorization_url(state="s", nonce=NONCE, code_verifier=VERIFIER)
    with pytest.raises(AuthenticationError):
        await oidc_provider.authenticate(code="made-up", code_verifier=VERIFIER, nonce=NONCE)


async def test_a_wrong_client_secret(idp: SimulatedIdp) -> None:
    provider = AuthlibOidcProvider(
        issuer=ISSUER,
        client_id=CLIENT_ID,
        client_secret="wrong",
        redirect_uri=REDIRECT_URI,
        transport=idp.transport,
    )
    with pytest.raises(AuthenticationError):
        await sign_in(provider, idp)


async def test_nothing_secret_is_logged(
    oidc_provider: AuthlibOidcProvider, idp: SimulatedIdp, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("DEBUG")
    idp.claim_overrides = {"nonce": "another nonce"}
    url = await oidc_provider.authorization_url(state="s" * 43, nonce=NONCE, code_verifier=VERIFIER)
    code = idp.consent(url, "subject-1")
    with pytest.raises(AuthenticationError):
        await oidc_provider.authenticate(code=code, code_verifier=VERIFIER, nonce=NONCE)
    text = caplog.text + str([record.__dict__ for record in caplog.records])
    for secret in (CLIENT_SECRET, VERIFIER, NONCE, code, "eyJ"):
        assert secret not in text
