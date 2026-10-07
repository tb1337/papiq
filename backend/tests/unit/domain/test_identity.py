from datetime import timedelta

import pytest

from papiq.core.domain.errors import ValidationError
from papiq.core.domain.identity import (
    ACCOUNT_THROTTLE,
    SOURCE_THROTTLE,
    Credential,
    LoginFailures,
    LoginMethod,
    Session,
    TotpSetting,
    check_new_password,
    csrf_matches,
    csrf_token,
    hash_token,
    new_recovery_codes,
    normalize_recovery_code,
)
from papiq.core.domain.ids import UserId, new_id
from tests.builders import NOW


def test_password_policy() -> None:
    assert check_new_password("twelve chars", "alice") == "twelve chars"
    fullwidth = "\uff21" * 12  # NFKC: twelve times "A"
    assert check_new_password(fullwidth, "alice") == "A" * 12
    for weak in ("eleven char", "x" * 257):
        with pytest.raises(ValidationError):
            check_new_password(weak, "alice")
    with pytest.raises(ValidationError):
        check_new_password("Alice-Wonder", "alice-wonder")


def test_recovery_codes() -> None:
    codes = new_recovery_codes()
    assert len(codes) == 10 and len(set(codes)) == 10
    assert all(len(code) == 19 and code.count("-") == 3 for code in codes)
    credential = Credential(
        user_id=UserId(new_id()),
        password_hash=None,
        recovery_codes={hash_token(normalize_recovery_code(code)) for code in codes},
    )
    assert credential.use_recovery_code(codes[0].lower().replace("-", " "))
    assert not credential.use_recovery_code(codes[0])
    assert not credential.use_recovery_code("")
    assert len(credential.recovery_codes) == 9


def test_totp_steps_are_used_once_and_in_order() -> None:
    credential = Credential(
        user_id=UserId(new_id()),
        password_hash=None,
        totp=TotpSetting(secret=b"x", confirmed=True, last_step=10),
    )
    assert not credential.accept_totp_step(10)
    assert not credential.accept_totp_step(9)
    assert credential.accept_totp_step(11)
    assert not credential.accept_totp_step(11)


def test_sessions() -> None:
    session, token = Session.start(
        user_id=UserId(new_id()),
        method=LoginMethod.PASSWORD,
        now=NOW,
        max_age=timedelta(days=30),
    )
    assert session.token_hash == hash_token(token) and token not in session.token_hash
    idle = timedelta(days=1)
    assert session.is_valid(NOW, idle)
    assert not session.is_valid(NOW + idle, idle)
    session.last_seen_at = NOW + timedelta(days=29, hours=23)
    assert not session.is_valid(NOW + timedelta(days=30), idle)


def test_csrf_tokens_belong_to_their_session() -> None:
    assert csrf_matches("session-a", csrf_token("session-a"))
    assert not csrf_matches("session-b", csrf_token("session-a"))
    assert not csrf_matches("session-a", "")


def test_account_throttle_doubles_up_to_fifteen_minutes() -> None:
    failures = LoginFailures.first("account:alice", NOW)
    blocks = []
    for _ in range(18):
        failures.record(ACCOUNT_THROTTLE, NOW)
        blocks.append(failures.retry_after(NOW))
    assert blocks[:5] == [None] * 5
    assert blocks[5:9] == [timedelta(seconds=s) for s in (1, 2, 4, 8)]
    assert blocks[-1] == timedelta(minutes=15)


def test_failures_count_within_their_window() -> None:
    failures = LoginFailures.first("account:alice", NOW)
    for _ in range(5):
        failures.record(ACCOUNT_THROTTLE, NOW)
    later = NOW + ACCOUNT_THROTTLE.window
    failures.record(ACCOUNT_THROTTLE, later)
    assert failures.failures == 1 and failures.retry_after(later) is None


def test_source_throttle_blocks_after_thirty_failures() -> None:
    failures = LoginFailures.first("source:192.0.2.1", NOW)
    for _ in range(29):
        failures.record(SOURCE_THROTTLE, NOW)
    assert failures.retry_after(NOW) is None
    failures.record(SOURCE_THROTTLE, NOW)
    assert failures.retry_after(NOW) == timedelta(minutes=15)
    assert failures.retry_after(NOW + timedelta(minutes=15)) is None
