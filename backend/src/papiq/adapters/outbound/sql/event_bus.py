"""Event bus on the outbox table, dispatched in process.

Each subscriber has a durable row in `event_subscriptions`: every event with `seq <= position`
has been handled, and the events whose handler failed wait in `event_retries`. The outbox
`seq` follows commit order (see `unit_of_work`), so moving the position forward never skips an
event that is committed later.

A dispatch reads events, calls the handler outside of any transaction (handlers may open
units of work themselves), then records the outcome in a short transaction. The position
moves by compare-and-set: if another dispatcher moved it meanwhile, nothing is recorded and
the events may be delivered twice, which at-least-once delivery allows.

`subscribe` cannot reach the database, so it notes the time. The subscription row is created
on the next dispatch, starting after the last event recorded before that time.
"""

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Row, delete, func, insert, select, update
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.ext.asyncio import AsyncConnection

from papiq.adapters.outbound.sql import events, tables
from papiq.adapters.outbound.sql.database import Database
from papiq.core.ports.event_bus import EventHandler

log = logging.getLogger(__name__)

_outbox = tables.outbox
_subscriptions = tables.event_subscriptions
_retries = tables.event_retries


@dataclass
class _Subscriber:
    handler: EventHandler
    since: datetime = field(default_factory=lambda: datetime.now(UTC))


class SqlEventBus:
    def __init__(self, database: Database) -> None:
        self._db = database
        self._subscribers: dict[str, _Subscriber] = {}
        self._lock = asyncio.Lock()

    def subscribe(self, subscriber: str, handler: EventHandler) -> None:
        if subscriber in self._subscribers:
            raise ValueError(f"subscriber {subscriber!r} is already registered")
        self._subscribers[subscriber] = _Subscriber(handler)

    async def dispatch(self, *, limit: int = 100) -> int:
        async with self._lock:
            delivered = 0
            for name, subscriber in self._subscribers.items():
                delivered += await self._dispatch_to(name, subscriber, limit)
            return delivered

    async def _dispatch_to(self, name: str, subscriber: _Subscriber, limit: int) -> int:
        position = await self._position(name, subscriber.since)
        async with self._db.reading() as connection:
            retries = (
                await connection.execute(
                    select(_outbox)
                    .join(_retries, _retries.c.seq == _outbox.c.seq)
                    .where(_retries.c.subscriber == name)
                    .order_by(_outbox.c.seq)
                    .limit(limit)
                )
            ).all()
            fresh = (
                await connection.execute(
                    select(_outbox)
                    .where(_outbox.c.seq > position)
                    .order_by(_outbox.c.seq)
                    .limit(limit)
                )
            ).all()

        retried, retries_failed = await self._deliver(name, subscriber.handler, retries)
        _, fresh_failed = await self._deliver(name, subscriber.handler, fresh)

        async with self._db.writing() as connection:
            if fresh:
                moved = await connection.execute(
                    update(_subscriptions)
                    .where(_subscriptions.c.name == name, _subscriptions.c.position == position)
                    .values(position=fresh[-1].seq)
                )
                if moved.rowcount == 0:
                    log.warning("subscription moved by another dispatcher", extra={"name": name})
                    return 0
                if fresh_failed:
                    await connection.execute(
                        insert(_retries),
                        [
                            {"subscriber": name, "seq": seq, "attempts": 1, "last_error": error}
                            for seq, error in fresh_failed.items()
                        ],
                    )
            if retried:
                await connection.execute(
                    delete(_retries).where(
                        _retries.c.subscriber == name, _retries.c.seq.in_(retried)
                    )
                )
            for seq, error in retries_failed.items():
                await connection.execute(
                    update(_retries)
                    .where(_retries.c.subscriber == name, _retries.c.seq == seq)
                    .values(attempts=_retries.c.attempts + 1, last_error=error)
                )
        return len(retried) + len(fresh) - len(fresh_failed)

    async def _deliver(
        self, name: str, handler: EventHandler, rows: Sequence[Row[Any]]
    ) -> tuple[list[int], dict[int, str]]:
        """Call the handler for each row; returns the delivered and the failed `seq`s."""
        delivered: list[int] = []
        failed: dict[int, str] = {}
        for row in rows:
            try:
                await handler(events.decode(row.type, row.event_id, row.occurred_at, row.payload))
            except Exception as error:
                log.exception(
                    "event handler failed",
                    extra={"subscriber": name, "event_id": str(row.event_id)},
                )
                failed[row.seq] = repr(error)
            else:
                delivered.append(row.seq)
        return delivered, failed

    async def _position(self, name: str, since: datetime) -> int:
        """The subscriber's position; creates the subscription if it does not exist yet."""
        async with self._db.reading() as connection:
            position = await _read_position(connection, name)
        if position is not None:
            return position
        async with self._db.writing() as connection:
            start = (
                select(func.coalesce(func.max(_outbox.c.seq), 0))
                .where(_outbox.c.recorded_at < since)
                .scalar_subquery()
            )
            dialect = sqlite if self._db.is_sqlite else postgresql
            await connection.execute(
                dialect.insert(_subscriptions)
                .values(name=name, position=start, created_at=datetime.now(UTC))
                .on_conflict_do_nothing(index_elements=[_subscriptions.c.name])
            )
            position = await _read_position(connection, name)
        assert position is not None
        log.info("event subscription created", extra={"name": name, "position": position})
        return position


async def _read_position(connection: AsyncConnection, name: str) -> int | None:
    row = (
        await connection.execute(
            select(_subscriptions.c.position).where(_subscriptions.c.name == name)
        )
    ).first()
    return None if row is None else int(row.position)
