from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from papiq.core.domain.errors import ValidationError
from papiq.core.domain.events import EVENT_TYPES
from papiq.core.domain.ids import DocumentId, EventId, UserId, new_id
from papiq.core.domain.webhooks import (
    ALL_EVENTS,
    DISABLED_FAILING,
    SIGNATURE_HEADER,
    Webhook,
    WebhookDelivery,
    event_body,
    new_secret,
    sign,
    signed_headers,
    validate_event_types,
    validate_url,
)
from tests.builders import NOW


def hook(**fields: object) -> Webhook:
    values: dict[str, object] = {
        "owner_id": UserId(new_id()),
        "name": "n8n",
        "url": "http://n8n.lan:5678/webhook/papiq",
        "event_types": [ALL_EVENTS],
        "encrypted_secret": b"secret-1",
        "now": NOW,
    }
    return Webhook.create(**{**values, **fields})  # type: ignore[arg-type]


# --- URL and event types ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://example.org/hook",
        "http://n8n.lan:5678/webhook/papiq?token=1",
        "http://192.168.1.20/hook",
        "http://[fd00::1]:8123/api/webhook/x",
        "  https://example.org/a  ",
    ],
)
def test_urls_with_http_or_https_and_a_host_are_valid_also_in_the_own_network(url: str) -> None:
    assert validate_url(url) == url.strip()


@pytest.mark.parametrize(
    "url",
    [
        "",
        "ftp://example.org/hook",
        "example.org/hook",
        "https:///hook",
        "https://user:pass@example.org/hook",
        "https://user@example.org/hook",
        "https://example.org/hook#frag",
        "https://example.org/a b",
        "https://example.org:99999/hook",
        "https://example.org/" + "a" * 2000,
        "javascript:alert(1)",
    ],
)
def test_other_urls_are_refused(url: str) -> None:
    with pytest.raises(ValidationError):
        validate_url(url)


def test_event_types_must_be_known_and_not_empty() -> None:
    assert validate_event_types(["document.filed", "document.deleted"]) == {
        "document.filed",
        "document.deleted",
    }
    assert validate_event_types(EVENT_TYPES) == frozenset(EVENT_TYPES)
    with pytest.raises(ValidationError):
        validate_event_types([])
    with pytest.raises(ValidationError, match=r"document\.fild"):
        validate_event_types(["document.fild"])


def test_all_events_swallows_the_others() -> None:
    assert validate_event_types([ALL_EVENTS, "document.filed"]) == {ALL_EVENTS}
    assert hook(event_types=[ALL_EVENTS]).wants("document.somethingnew")
    assert not hook(event_types=["document.filed"]).wants("document.updated")


# --- signature ----------------------------------------------------------------------------------


def test_the_signature_matches_the_standard_webhooks_example() -> None:
    signature = sign(
        "whsec_MfKQ9r8GKYqrTwjUPD8ILPZIo2LaLaSw",
        "msg_p5jXN8AQM9LWM0D4loKWxJek",
        1614265330,
        b'{"test": 2432232314}',
    )
    assert signature == "v1,g0hM9SsE+OTPJTGt/tmIKtSyZlE3uFJELVlNIOLJ1OE="


def test_a_request_carries_one_signature_per_secret_in_use() -> None:
    first, second = new_secret(), new_secret()
    body = b'{"id":"e"}'
    headers = signed_headers([first, second], "e", 1700000000, body)
    assert headers["webhook-id"] == "e"
    assert headers["webhook-timestamp"] == "1700000000"
    assert headers[SIGNATURE_HEADER].split(" ") == [
        sign(first, "e", 1700000000, body),
        sign(second, "e", 1700000000, body),
    ]


def test_new_secrets_are_random_and_have_the_prefix() -> None:
    a, b = new_secret(), new_secret()
    assert a != b
    assert a.startswith("whsec_") and len(a) > 40


def test_the_body_is_thin_compact_json() -> None:
    event, document = EventId(UUID(int=1)), DocumentId(UUID(int=2))
    body = event_body(
        event_id=event,
        type="document.filed",
        occurred_at=datetime(2026, 10, 8, 6, 36, 46, 123456, tzinfo=UTC),
        document_id=document,
    )
    assert body == (
        b'{"id":"00000000-0000-0000-0000-000000000001","type":"document.filed",'
        b'"occurred_at":"2026-10-08T06:36:46Z",'
        b'"document_id":"00000000-0000-0000-0000-000000000002"}'
    )
    test = event_body(event_id=event, type="webhook.test", occurred_at=NOW, document_id=None)
    assert test.endswith(b'"document_id":null}')


# --- the aggregate ------------------------------------------------------------------------------


def test_a_webhook_validates_its_fields() -> None:
    with pytest.raises(ValidationError):
        hook(name="  ")
    with pytest.raises(ValidationError):
        hook(name="x" * 101)
    with pytest.raises(ValidationError):
        hook(url="ftp://x")
    with pytest.raises(ValidationError):
        hook(event_types=[])


def test_change_updates_what_is_given_and_switching_on_clears_the_failures() -> None:
    webhook = hook()
    webhook.failed_streak = 3
    webhook.active, webhook.disabled_reason = False, DISABLED_FAILING
    later = NOW + timedelta(hours=1)
    webhook.change(now=later, name="HA", event_types=["document.filed"])
    assert (webhook.name, webhook.event_types, webhook.updated_at) == (
        "HA",
        frozenset({"document.filed"}),
        later,
    )
    assert not webhook.active and webhook.failed_streak == 3
    webhook.change(now=later, active=True)
    assert webhook.active and webhook.failed_streak == 0 and webhook.disabled_reason is None


def test_the_old_secret_signs_next_to_the_new_one_during_the_grace_period() -> None:
    webhook = hook()
    webhook.renew_secret(b"secret-2", now=NOW, grace=timedelta(hours=24))
    assert webhook.encrypted_secrets(NOW + timedelta(hours=23)) == [b"secret-2", b"secret-1"]
    assert webhook.encrypted_secrets(NOW + timedelta(hours=24)) == [b"secret-2"]
    webhook.renew_secret(b"secret-3", now=NOW, grace=timedelta(hours=24))
    assert webhook.encrypted_secrets(NOW) == [b"secret-3", b"secret-2"]


def test_repeated_failures_switch_a_webhook_off_and_a_success_resets_the_count() -> None:
    webhook = hook()
    assert not webhook.gave_up(disable_after=3, now=NOW)
    assert not webhook.gave_up(disable_after=3, now=NOW)
    webhook.delivered()
    assert webhook.failed_streak == 0
    for _ in range(2):
        assert not webhook.gave_up(disable_after=3, now=NOW)
    assert webhook.gave_up(disable_after=3, now=NOW)
    assert not webhook.active and webhook.disabled_reason == DISABLED_FAILING


def test_a_delivery_keeps_only_a_short_error() -> None:
    delivery = WebhookDelivery.record(
        webhook_id=hook().id,
        event_id=EventId(new_id()),
        event_type="document.filed",
        document_id=None,
        attempt=1,
        started_at=NOW,
        duration_ms=5,
        outcome="retrying",
        error="x" * 1000,
    )
    assert delivery.error is not None and len(delivery.error) == 300
