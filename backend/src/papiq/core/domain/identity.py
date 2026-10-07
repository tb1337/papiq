"""Identity: how users prove who they are. Kept apart from `User`, which knows nothing of it.

- Passwords are stored as hashes (Argon2id, see the `PasswordHasher` port), checked against a
  minimum policy and normalised (Unicode NFKC) before hashing and verifying.
- TOTP is optional per user. Its secret must stay readable, so it is stored encrypted (see the
  `SecretCipher` port). A code is accepted once: its time step must be later than the last one
  used. Recovery codes are stored as SHA-256 hashes and work once each.
- Sessions (web UI) and API tokens (CLI, MCP) are random 256-bit values; only their SHA-256
  hashes are stored. They are high-entropy, so a fast hash suffices.
- A user can be linked to accounts at an external identity provider (OIDC) by issuer and
  subject, never by e-mail address.
- Failed sign-ins are counted per account and per source to slow down guessing.
"""

import base64
import hashlib
import hmac
import secrets
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Self

from papiq.core.domain.errors import ValidationError
from papiq.core.domain.ids import (
    ApiTokenId,
    ExternalIdentityId,
    SessionId,
    UserId,
    new_id,
)
from papiq.core.domain.validation import name_key, require_name, require_utc

PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 256

API_TOKEN_PREFIX = "papiq_"
RECOVERY_CODE_COUNT = 10
_RECOVERY_CODE_BYTES = 10  # 80 bits, 16 base32 characters
_TOKEN_BYTES = 32  # 256 bits


# --- passwords ----------------------------------------------------------------------------------


def normalize_password(password: str) -> str:
    """The form that is hashed: NFKC, so that equal-looking input from different keyboards
    matches."""
    return unicodedata.normalize("NFKC", password)


def check_new_password(password: str, username: str) -> str:
    """The normalised password if it meets the policy (NIST SP 800-63B): 12 to 256 characters
    and not the username. No rules on character classes."""
    normalized = normalize_password(password)
    if len(normalized) < PASSWORD_MIN_LENGTH:
        raise ValidationError(f"the password must have at least {PASSWORD_MIN_LENGTH} characters")
    if len(normalized) > PASSWORD_MAX_LENGTH:
        raise ValidationError(f"the password must have at most {PASSWORD_MAX_LENGTH} characters")
    if name_key(normalized) == name_key(username):
        raise ValidationError("the password must not be the username")
    return normalized


# --- tokens and codes ---------------------------------------------------------------------------


def new_token(prefix: str = "") -> str:
    """A random token of 256 bits, URL-safe."""
    return prefix + secrets.token_urlsafe(_TOKEN_BYTES)


def hash_token(token: str) -> str:
    """The stored form of a session token, API token or recovery code: SHA-256, hex."""
    return hashlib.sha256(token.encode()).hexdigest()


def hash_matches(stored: str, token: str) -> bool:
    return hmac.compare_digest(stored, hash_token(token))


def new_recovery_codes() -> list[str]:
    """Recovery codes as shown once to the user: `ABCD-EFGH-IJKL-MNOP` (80 bits each)."""
    codes = []
    for _ in range(RECOVERY_CODE_COUNT):
        raw = base64.b32encode(secrets.token_bytes(_RECOVERY_CODE_BYTES)).decode()
        codes.append("-".join(raw[i : i + 4] for i in range(0, len(raw), 4)))
    return codes


def normalize_recovery_code(code: str) -> str:
    """Upper case, without separators and spaces: the form that is hashed."""
    return "".join(char for char in code.upper() if char.isalnum())


# --- credentials --------------------------------------------------------------------------------


@dataclass(kw_only=True)
class TotpSetting:
    """`secret` is encrypted. Until `confirmed`, TOTP is being set up and not required."""

    secret: bytes
    confirmed: bool = False
    last_step: int | None = None  # time step of the last accepted code


@dataclass(kw_only=True)
class Credential:
    """What a user signs in with. `password_hash` is None for accounts created through an
    identity provider that have no local password."""

    user_id: UserId
    password_hash: str | None
    password_changed_at: datetime | None = None
    totp: TotpSetting | None = None
    recovery_codes: set[str] = field(default_factory=set)  # hashes of unused codes
    version: int = 1

    @property
    def id(self) -> UserId:
        return self.user_id

    @property
    def totp_enabled(self) -> bool:
        return self.totp is not None and self.totp.confirmed

    def use_recovery_code(self, code: str) -> bool:
        """Consume a recovery code; False if it is not one of the unused codes."""
        normalized = normalize_recovery_code(code)
        for stored in list(self.recovery_codes):
            if hash_matches(stored, normalized):
                self.recovery_codes.discard(stored)
                return True
        return False

    def accept_totp_step(self, step: int) -> bool:
        """Record the step of a valid code; False if it, or a later one, was used already."""
        assert self.totp is not None
        if self.totp.last_step is not None and step <= self.totp.last_step:
            return False
        self.totp.last_step = step
        return True


# --- sessions and API tokens --------------------------------------------------------------------


class LoginMethod(StrEnum):
    PASSWORD = "password"
    OIDC = "oidc"


@dataclass(kw_only=True)
class Session:
    """A signed-in browser. Ends at `expires_at`, or earlier when unused for the idle time."""

    id: SessionId
    user_id: UserId
    token_hash: str
    method: LoginMethod
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    version: int = 1

    def __post_init__(self) -> None:
        self.created_at = require_utc(self.created_at, "created_at")
        self.last_seen_at = require_utc(self.last_seen_at, "last_seen_at")
        self.expires_at = require_utc(self.expires_at, "expires_at")

    @classmethod
    def start(
        cls, *, user_id: UserId, method: LoginMethod, now: datetime, max_age: timedelta
    ) -> tuple[Self, str]:
        """A new session and its token, which only the client keeps."""
        token = new_token()
        session = cls(
            id=SessionId(new_id()),
            user_id=user_id,
            token_hash=hash_token(token),
            method=method,
            created_at=now,
            last_seen_at=now,
            expires_at=now + max_age,
        )
        return session, token

    def is_valid(self, now: datetime, idle: timedelta) -> bool:
        return now < self.expires_at and now - self.last_seen_at < idle


def csrf_token(session_token: str) -> str:
    """The CSRF token of a session, derived from its token, so nothing more is stored. Only
    someone who knows the session token (sent in an HTTP-only cookie) can compute it."""
    return hmac.new(session_token.encode(), b"papiq-csrf", hashlib.sha256).hexdigest()


def csrf_matches(session_token: str, candidate: str) -> bool:
    return hmac.compare_digest(csrf_token(session_token), candidate)


class TokenScope(StrEnum):
    READ = "read"
    READ_WRITE = "read_write"

    @property
    def can_write(self) -> bool:
        return self is TokenScope.READ_WRITE


@dataclass(kw_only=True)
class ApiToken:
    """A personal API token. The token itself is shown once, at creation."""

    id: ApiTokenId
    user_id: UserId
    name: str
    scope: TokenScope
    token_hash: str
    created_at: datetime
    expires_at: datetime | None = None
    last_used_at: datetime | None = None
    version: int = 1

    def __post_init__(self) -> None:
        self.name = require_name(self.name, "token name")
        if len(self.name) > 100:
            raise ValidationError("the token name must have at most 100 characters")
        self.created_at = require_utc(self.created_at, "created_at")
        if self.expires_at is not None:
            self.expires_at = require_utc(self.expires_at, "expires_at")

    @classmethod
    def issue(
        cls,
        *,
        user_id: UserId,
        name: str,
        scope: TokenScope,
        now: datetime,
        expires_at: datetime | None = None,
    ) -> tuple[Self, str]:
        if expires_at is not None and require_utc(expires_at, "expires_at") <= now:
            raise ValidationError("the expiry must be in the future")
        token = new_token(API_TOKEN_PREFIX)
        api_token = cls(
            id=ApiTokenId(new_id()),
            user_id=user_id,
            name=name,
            scope=scope,
            token_hash=hash_token(token),
            created_at=now,
            expires_at=expires_at,
        )
        return api_token, token

    def is_valid(self, now: datetime) -> bool:
        return self.expires_at is None or now < self.expires_at


# --- external identities ------------------------------------------------------------------------


@dataclass(kw_only=True)
class ExternalIdentity:
    """A link between a local user and an account at an identity provider (OIDC), by the
    provider's issuer and its subject for that account. Unique per issuer and subject."""

    id: ExternalIdentityId
    issuer: str
    subject: str
    user_id: UserId
    created_at: datetime
    version: int = 1

    @classmethod
    def link(cls, *, issuer: str, subject: str, user_id: UserId, now: datetime) -> Self:
        return cls(
            id=ExternalIdentityId(new_id()),
            issuer=require_name(issuer, "issuer"),
            subject=require_name(subject, "subject"),
            user_id=user_id,
            created_at=require_utc(now, "created_at"),
        )


@dataclass(frozen=True)
class OidcIdentity:
    """Who an identity provider says signed in: the account is `issuer` and `subject`; the
    other claims are hints (e.g. the username for an account created on first sign-in)."""

    issuer: str
    subject: str
    username: str | None = None
    email: str | None = None
    name: str | None = None


def safe_redirect(target: str | None) -> str:
    """`target` if it is a path on this site (`/inbox?x=1`), else `/`. Guards the redirect after
    signing in through an identity provider against sending the browser elsewhere."""
    if (
        not target
        or not target.startswith("/")
        or target.startswith("//")
        or "\\" in target
        or any(ord(char) < 0x21 or ord(char) == 0x7F for char in target)
        or len(target) > 2000
    ):
        return "/"
    return target


# --- guessing -----------------------------------------------------------------------------------

_FAILURE_NAMESPACE = uuid.UUID("0199a6c4-5d1e-7b2a-9c3d-4e5f60718293")


@dataclass(frozen=True)
class ThrottleRule:
    """`free` failures within `window` cost nothing; each further one blocks for
    `base * 2^(n - free - 1)`, at most `limit`. With `base == limit` the block is fixed."""

    free: int
    window: timedelta
    base: timedelta
    limit: timedelta

    def block(self, failures: int) -> timedelta | None:
        excess = failures - self.free
        if excess <= 0:
            return None
        return min(self.base * (1 << min(excess - 1, 30)), self.limit)


# Per account (also for unknown usernames): back off from the sixth failure on, doubling from
# one second to 15 minutes. No hard lock, so nobody can lock others out on purpose.
ACCOUNT_THROTTLE = ThrottleRule(
    free=5, window=timedelta(days=1), base=timedelta(seconds=1), limit=timedelta(minutes=15)
)
# Per source address: 30 failures within 15 minutes block the source for 15 minutes.
SOURCE_THROTTLE = ThrottleRule(
    free=29, window=timedelta(minutes=15), base=timedelta(minutes=15), limit=timedelta(minutes=15)
)


def account_key(username: str) -> str:
    return "account:" + name_key(username)


def source_key(address: str) -> str:
    return "source:" + address


@dataclass(kw_only=True)
class LoginFailures:
    """Failed sign-ins under one key (`account:<name>` or `source:<address>`)."""

    key: str
    failures: int
    first_failure_at: datetime
    blocked_until: datetime | None = None
    version: int = 1

    @property
    def id(self) -> uuid.UUID:
        return failure_id(self.key)

    @classmethod
    def first(cls, key: str, now: datetime) -> Self:
        return cls(key=key, failures=0, first_failure_at=now)

    def retry_after(self, now: datetime) -> timedelta | None:
        """How long further attempts are refused, if they are."""
        if self.blocked_until is None or self.blocked_until <= now:
            return None
        return self.blocked_until - now

    def record(self, rule: ThrottleRule, now: datetime) -> None:
        if now - self.first_failure_at >= rule.window:
            self.failures, self.first_failure_at = 0, now
        self.failures += 1
        block = rule.block(self.failures)
        self.blocked_until = None if block is None else now + block


def failure_id(key: str) -> uuid.UUID:
    """A stable id for a failure key (for repositories that key rows by UUID)."""
    return uuid.uuid5(_FAILURE_NAMESPACE, key)
