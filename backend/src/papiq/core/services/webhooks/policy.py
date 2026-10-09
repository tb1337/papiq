"""Tuning of webhooks."""

from dataclasses import dataclass
from datetime import timedelta


@dataclass(frozen=True)
class WebhookPolicy:
    per_user: int = 20  # webhooks a user may have
    secret_grace: timedelta = timedelta(hours=24)  # the old secret still signs after renewing
    max_attempts: int = 10  # attempts per delivery
    retry_delay: timedelta = timedelta(seconds=30)  # before the second attempt, then doubling
    max_retry_delay: timedelta = timedelta(hours=1)
    disable_after: int = 20  # deliveries given up in a row until the webhook is switched off
    timeout: timedelta = timedelta(seconds=10)  # per attempt
    # Test requests a user may send per window (they go out at once, to any address).
    test_requests: int = 10
    test_window: timedelta = timedelta(minutes=1)

    def __post_init__(self) -> None:
        if min(self.per_user, self.max_attempts, self.disable_after, self.test_requests) < 1:
            raise ValueError("limits must be at least 1")
