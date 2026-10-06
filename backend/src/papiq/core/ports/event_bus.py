from typing import Protocol


class EventBus(Protocol):
    """Publishes domain events and delivers them to subscribers at least once.

    Events are written in the same transaction as the state change (transactional outbox).
    First adapter: outbox table in the database with in-process dispatch.
    Later adapters: Valkey Streams, NATS.
    """
