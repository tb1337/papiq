from typing import Protocol


class IdentityProvider(Protocol):
    """Authenticates users. First adapters: native login (Argon2id, optional TOTP), OIDC."""
