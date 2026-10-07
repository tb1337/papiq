"""Identity: stored credentials, sessions, API tokens and links to identity providers, all
reached through the unit of work; and the cryptography they need.

Common rules for the repositories (as in `repository.py`): reads return copies; `update`
checks the version; uniqueness (session and token hashes; issuer and subject of an external
identity) raises ConflictError at the latest on commit. `touch` only moves a usage timestamp
forward and does not count as a change: it neither checks nor increments the version, so
concurrent requests never conflict on it. `remove` of something missing is a no-op.
"""

from datetime import datetime, timedelta
from typing import Protocol

from papiq.core.domain.identity import (
    ApiToken,
    Credential,
    ExternalIdentity,
    LoginFailures,
    OidcIdentity,
    Session,
    ThrottleRule,
)
from papiq.core.domain.ids import ApiTokenId, ExternalIdentityId, SessionId, UserId


class CredentialRepository(Protocol):
    """One credential per user."""

    async def get(self, user: UserId) -> Credential: ...

    async def find(self, user: UserId) -> Credential | None: ...

    async def add(self, credential: Credential) -> None: ...

    async def update(self, credential: Credential) -> None: ...

    async def remove(self, user: UserId) -> None: ...


class SessionRepository(Protocol):
    async def add(self, session: Session) -> None: ...

    async def find_by_token(self, token_hash: str) -> Session | None: ...

    async def touch(self, id: SessionId, last_seen_at: datetime) -> None: ...

    async def remove(self, id: SessionId) -> None: ...

    async def remove_for_user(self, user: UserId, *, keep: SessionId | None = None) -> int:
        """End all sessions of the user except `keep`; returns how many ended."""
        ...

    async def purge(self, *, now: datetime, idle_before: datetime) -> int:
        """Remove sessions that expired (`expires_at <= now`) or were last seen at or before
        `idle_before`; returns how many."""
        ...


class ApiTokenRepository(Protocol):
    async def add(self, token: ApiToken) -> None: ...

    async def find(self, id: ApiTokenId) -> ApiToken | None: ...

    async def find_by_token(self, token_hash: str) -> ApiToken | None: ...

    async def list_for(self, user: UserId) -> list[ApiToken]:
        """The user's tokens, oldest first."""
        ...

    async def touch(self, id: ApiTokenId, last_used_at: datetime) -> None: ...

    async def remove(self, id: ApiTokenId) -> None: ...

    async def remove_for_user(self, user: UserId) -> int: ...


class ExternalIdentityRepository(Protocol):
    async def add(self, identity: ExternalIdentity) -> None: ...

    async def find(self, issuer: str, subject: str) -> ExternalIdentity | None: ...

    async def list_for(self, user: UserId) -> list[ExternalIdentity]: ...

    async def remove(self, id: ExternalIdentityId) -> None: ...

    async def remove_for_user(self, user: UserId) -> int: ...


class LoginFailureRepository(Protocol):
    """Counts of failed sign-ins. Attempts are counted atomically before they are checked
    (`reserve`) and taken back if they did not fail (`release`), so attempts that arrive at
    the same time cannot pass a throttle together."""

    async def find(self, key: str) -> LoginFailures | None: ...

    async def reserve(self, key: str, rule: ThrottleRule, now: datetime) -> timedelta | None:
        """Atomically: if `key` is blocked at `now`, count nothing and return how long it
        stays blocked. Otherwise count one attempt (a new window if the last one has passed),
        set the block `rule` gives for the new count as if the attempt fails, return None."""
        ...

    async def release(self, key: str, rule: ThrottleRule) -> None:
        """Take back one attempt counted by `reserve` (it did not fail); lift the block if
        `rule` gives none for the remaining count."""
        ...

    async def remove(self, key: str) -> None: ...

    async def purge(self, *, before: datetime) -> int:
        """Remove entries whose first failure is before `before` and that block no longer
        (`blocked_until` unset or before `before`); returns how many."""
        ...


# --- cryptography -------------------------------------------------------------------------------


class PasswordHasher(Protocol):
    """Hashes passwords for storage. First adapter: Argon2id (argon2-cffi).

    Hashing is slow on purpose; adapters run it outside the event loop. A hash carries its
    parameters, so hashes made with older parameters still verify and `needs_rehash` tells
    when to replace them.
    """

    async def hash(self, password: str) -> str: ...

    async def verify(self, hash: str, password: str) -> bool:
        """False for a wrong password or a malformed hash; never raises for those."""
        ...

    def needs_rehash(self, hash: str) -> bool: ...


class DecryptionError(Exception):
    """The ciphertext is damaged, belongs to another context or another key."""


class SecretCipher(Protocol):
    """Authenticated encryption of small secrets at rest (TOTP secrets). First adapter:
    AES-256-GCM with the configured key. `context` is bound to the ciphertext (e.g. the user
    id), so a ciphertext copied to another context does not decrypt."""

    def encrypt(self, plaintext: bytes, *, context: bytes) -> bytes: ...

    def decrypt(self, ciphertext: bytes, *, context: bytes) -> bytes:
        """Raises DecryptionError."""
        ...


class Totp(Protocol):
    """Time-based one-time passwords (RFC 6238: 6 digits, 30-second steps). First adapter:
    pyotp."""

    def new_secret(self) -> str:
        """A new random secret, base32."""
        ...

    def code(self, secret: str, at: datetime) -> str:
        """The code valid at `at`."""
        ...

    def matching_step(self, secret: str, code: str, at: datetime) -> int | None:
        """The time step (Unix time // 30) whose code is `code`, looking one step before and
        after `at` to allow for clock drift; None if none matches."""
        ...

    def provisioning_uri(self, secret: str, *, account: str, issuer: str) -> str:
        """`otpauth://` URI for authenticator apps (QR code)."""
        ...


# --- identity provider --------------------------------------------------------------------------


class OidcProvider(Protocol):
    """An OpenID Connect provider, Authorization Code Flow with PKCE (S256). First adapter:
    Authlib with joserfc.

    The adapter knows its client registration (id, secret, scopes, redirect URI). It checks the
    ID token completely: signature against the provider's published keys (asymmetric algorithms
    only), issuer, audience (and `azp` with several audiences), expiry, issue time and nonce.
    """

    @property
    def issuer(self) -> str: ...

    async def authorization_url(self, *, state: str, nonce: str, code_verifier: str) -> str:
        """Where to send the browser to sign in."""
        ...

    async def authenticate(self, *, code: str, code_verifier: str, nonce: str) -> OidcIdentity:
        """Exchange the code for tokens and check the ID token. AuthenticationError if the
        provider refuses or the token is not valid; IdentityProviderError if the provider
        cannot be reached or answers unusably."""
        ...
