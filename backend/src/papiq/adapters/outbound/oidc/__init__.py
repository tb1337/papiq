"""OpenID Connect client: Authlib (OAuth 2.0, PKCE) over httpx2, ID tokens checked with joserfc."""

from papiq.adapters.outbound.oidc.provider import AuthlibOidcProvider

__all__ = ["AuthlibOidcProvider"]
