"""Webhooks: a user's subscription to document events, and the log of the deliveries.

A webhook has a target URL, the event types it wants (or all of them) and a secret that signs
every request. The secret is stored encrypted (it is needed to sign, so a hash would not do);
the plain text exists only in the answer to creating or renewing it.

Requests follow the Standard Webhooks scheme (https://www.standardwebhooks.com): the headers
`webhook-id` (the event id, for receivers to recognise repetitions), `webhook-timestamp`
(Unix seconds of the attempt) and `webhook-signature` (`v1,<base64 HMAC-SHA256>` of
`<id>.<timestamp>.<body>`, several separated by spaces while a renewed secret still has its
predecessor alongside). The body is thin: the event's type, id, time and document.
"""

import base64
import binascii
import hashlib
import hmac
import json
import secrets
from collections.abc import Collection, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from urllib.parse import urlsplit
from uuid import UUID

from papiq.core.domain.errors import ValidationError
from papiq.core.domain.events import EVENT_TYPES
from papiq.core.domain.ids import DeliveryId, DocumentId, EventId, UserId, WebhookId, new_id
from papiq.core.domain.validation import require_name, require_utc

ALL_EVENTS = "*"
"""In `event_types`: every event type, also those that come later."""
TEST_EVENT = "webhook.test"
"""The event type of the test request; it has no document."""

SECRET_PREFIX = "whsec_"
SECRET_BYTES = 32
MAX_URL_LENGTH = 2000
MAX_NAME_LENGTH = 100
MAX_ERROR_LENGTH = 300
DISABLED_FAILING = "failing"
"""`Webhook.disabled_reason` of a webhook that Papiq switched off after repeated failures."""

SIGNATURE_VERSION = "v1"
ID_HEADER = "webhook-id"
TIMESTAMP_HEADER = "webhook-timestamp"
SIGNATURE_HEADER = "webhook-signature"


def event_type_names() -> list[str]:
    return sorted(EVENT_TYPES)


def validate_url(url: str) -> str:
    """The target URL: `http` or `https` with a host, no credentials, no fragment. Targets in
    the own network are fine. ValidationError otherwise."""
    url = url.strip()
    if not url or len(url) > MAX_URL_LENGTH:
        raise ValidationError(f"the URL must have 1 to {MAX_URL_LENGTH} characters")
    if any(ord(char) < 33 or ord(char) == 127 for char in url):
        raise ValidationError("the URL must not contain spaces or control characters")
    try:
        parts = urlsplit(url)
        parts.port  # noqa: B018  (raises ValueError for a bad port)
    except ValueError:
        raise ValidationError("the URL is not valid") from None
    if parts.scheme not in ("http", "https"):
        raise ValidationError("the URL must start with http:// or https://")
    if not parts.hostname:
        raise ValidationError("the URL needs a host")
    if parts.username is not None or parts.password is not None:
        raise ValidationError("the URL must not contain a user name or password")
    if parts.fragment:
        raise ValidationError("the URL must not have a fragment")
    return url


def validate_event_types(types: Collection[str]) -> frozenset[str]:
    """The known event types, or `*` (then only that). ValidationError for none or unknown."""
    chosen = frozenset(types)
    if not chosen:
        raise ValidationError("choose at least one event type")
    unknown = sorted(chosen - EVENT_TYPES.keys() - {ALL_EVENTS})
    if unknown:
        raise ValidationError(
            f"unknown event types: {', '.join(unknown)}; "
            f"known: {', '.join(event_type_names())} or {ALL_EVENTS}"
        )
    return frozenset({ALL_EVENTS}) if ALL_EVENTS in chosen else chosen


def new_secret() -> str:
    """A new signing secret: `whsec_` and 32 random bytes in Base64."""
    return SECRET_PREFIX + base64.b64encode(secrets.token_bytes(SECRET_BYTES)).decode("ascii")


def _key(secret: str) -> bytes:
    if not secret.startswith(SECRET_PREFIX):
        raise ValueError("not a webhook secret")
    try:
        return base64.b64decode(secret.removeprefix(SECRET_PREFIX), validate=True)
    except binascii.Error:
        raise ValueError("not a webhook secret") from None


def sign(secret: str, message_id: str, timestamp: int, body: bytes) -> str:
    """`v1,<base64>`: HMAC-SHA256 of `<message_id>.<timestamp>.<body>` with the secret's key."""
    content = f"{message_id}.{timestamp}.".encode() + body
    digest = hmac.new(_key(secret), content, hashlib.sha256).digest()
    return f"{SIGNATURE_VERSION},{base64.b64encode(digest).decode('ascii')}"


def signed_headers(
    secrets_in_use: Iterable[str], message_id: str, timestamp: int, body: bytes
) -> dict[str, str]:
    """The headers of a request: id, timestamp and one signature per secret in use."""
    return {
        ID_HEADER: message_id,
        TIMESTAMP_HEADER: str(timestamp),
        SIGNATURE_HEADER: " ".join(
            sign(secret, message_id, timestamp, body) for secret in secrets_in_use
        ),
    }


def event_body(
    *, event_id: EventId | UUID, type: str, occurred_at: datetime, document_id: DocumentId | None
) -> bytes:
    """The thin body of a request: compact JSON in UTF-8, signed exactly as sent."""
    occurred_at = require_utc(occurred_at, "occurred_at")
    payload = {
        "id": str(event_id),
        "type": type,
        "occurred_at": occurred_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "document_id": None if document_id is None else str(document_id),
    }
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


@dataclass(kw_only=True)
class Webhook:
    """A user's subscription. `encrypted_secret` is the secret as `SecretCipher` made it;
    `previous_secret` still signs next to it until `previous_valid_until`."""

    id: WebhookId
    owner_id: UserId
    name: str
    url: str
    event_types: frozenset[str]
    encrypted_secret: bytes = field(repr=False)
    active: bool = True
    disabled_reason: str | None = None
    failed_streak: int = 0
    previous_secret: bytes | None = field(default=None, repr=False)
    previous_valid_until: datetime | None = None
    created_at: datetime
    updated_at: datetime
    version: int = 1

    def __post_init__(self) -> None:
        self.name = require_name(self.name, "webhook name")
        if len(self.name) > MAX_NAME_LENGTH:
            raise ValidationError(f"the name must have at most {MAX_NAME_LENGTH} characters")
        self.url = validate_url(self.url)
        self.event_types = validate_event_types(self.event_types)
        self.created_at = require_utc(self.created_at, "created_at")
        self.updated_at = require_utc(self.updated_at, "updated_at")

    @classmethod
    def create(
        cls,
        *,
        owner_id: UserId,
        name: str,
        url: str,
        event_types: Collection[str],
        encrypted_secret: bytes,
        now: datetime,
        active: bool = True,
    ) -> "Webhook":
        return cls(
            id=WebhookId(new_id()),
            owner_id=owner_id,
            name=name,
            url=url,
            event_types=frozenset(event_types),
            encrypted_secret=encrypted_secret,
            active=active,
            created_at=now,
            updated_at=now,
        )

    def wants(self, event_type: str) -> bool:
        return ALL_EVENTS in self.event_types or event_type in self.event_types

    def change(
        self,
        *,
        now: datetime,
        name: str | None = None,
        url: str | None = None,
        event_types: Collection[str] | None = None,
        active: bool | None = None,
    ) -> None:
        """Change what is given. Switching on (again) clears the failures and the reason."""
        if name is not None:
            self.name = require_name(name, "webhook name")
            if len(self.name) > MAX_NAME_LENGTH:
                raise ValidationError(f"the name must have at most {MAX_NAME_LENGTH} characters")
        if url is not None:
            self.url = validate_url(url)
        if event_types is not None:
            self.event_types = validate_event_types(event_types)
        if active is not None:
            if active and not self.active:
                self.failed_streak = 0
            self.active = active
            self.disabled_reason = None
        self.updated_at = now

    def renew_secret(self, encrypted_secret: bytes, *, now: datetime, grace: timedelta) -> None:
        """A new secret; the old one keeps signing for `grace` (renewing again drops it)."""
        self.previous_secret = self.encrypted_secret
        self.previous_valid_until = now + grace
        self.encrypted_secret = encrypted_secret
        self.updated_at = now

    def encrypted_secrets(self, now: datetime) -> list[bytes]:
        """The secrets that sign a request now: the current one, and the previous one during
        the grace period."""
        current = [self.encrypted_secret]
        if (
            self.previous_secret is not None
            and self.previous_valid_until is not None
            and now < self.previous_valid_until
        ):
            current.append(self.previous_secret)
        return current

    def delivered(self) -> None:
        self.failed_streak = 0

    def gave_up(self, *, disable_after: int, now: datetime) -> bool:
        """A delivery was given up. Returns True if this switched the webhook off."""
        self.failed_streak += 1
        if self.active and self.failed_streak >= disable_after:
            self.active = False
            self.disabled_reason = DISABLED_FAILING
            self.updated_at = now
            return True
        return False


class DeliveryOutcome(StrEnum):
    DELIVERED = "delivered"  # answered with 2xx
    RETRYING = "retrying"  # the attempt failed, another follows
    GAVE_UP = "gave_up"  # failed for good
    DROPPED = "dropped"  # not sent: the right to see the document is gone, or the webhook is off


@dataclass(kw_only=True)
class WebhookDelivery:
    """One attempt to deliver an event. The answer of the receiver is not kept."""

    id: DeliveryId
    webhook_id: WebhookId
    event_id: EventId
    event_type: str
    document_id: DocumentId | None
    attempt: int
    started_at: datetime
    duration_ms: int
    outcome: DeliveryOutcome
    status_code: int | None = None
    error: str | None = None
    next_attempt_at: datetime | None = None

    def __post_init__(self) -> None:
        self.started_at = require_utc(self.started_at, "started_at")
        if self.next_attempt_at is not None:
            self.next_attempt_at = require_utc(self.next_attempt_at, "next_attempt_at")
        if self.error is not None:
            self.error = self.error[:MAX_ERROR_LENGTH]

    @classmethod
    def record(cls, **fields: object) -> "WebhookDelivery":
        return cls(id=DeliveryId(new_id()), **fields)  # type: ignore[arg-type]
