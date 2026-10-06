from typing import Protocol


class JobQueue(Protocol):
    """Retryable, idempotent pipeline steps. First adapter: job table in the database."""
