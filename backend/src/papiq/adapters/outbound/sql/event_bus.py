"""Event bus on the outbox table, dispatched in process.

Each subscriber has a durable row in `event_subscriptions`: every event with `seq <= position`
has been handled, and the events whose handler failed wait in `event_retries`. The outbox
`seq` follows commit order (see `unit_of_work`), so moving the position forward never skips an
event that is committed later.

A dispatch reads events, calls the handler outside of any transaction (handlers may open
units of work themselves), then records the outcome in a short transaction. The position
moves by compare-and-set: if another dispatcher moved it meanwhile, nothing is recorded and
the events may be delivered twice, which at-least-once delivery allows. Failed events are
due again at `retry_at` (`DeliveryRetry`: growing delay, limited attempts; given up events are
removed from `event_retries` and logged). Due retries go fewest attempts first, so events that
keep failing do not starve newer failures.

`purge` deletes events below the lowest subscription position that no subscriber still retries.

Rows of an unknown event type (written by a newer version) cannot be turned into events; they
are logged and skipped, as receivers ignore unknown types anyway.

`subscribe` cannot reach the database, so it notes the time. The subscription row is created
on the next dispatch, starting after the last event recorded before that time. Both times
come from the system clocks of the processes involved; with clock skew between hosts, a *new*
subscription may start a little early (harmless) or late. Existing subscriptions resume by
position and are not affected.
"""

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Row, delete, exists, func, insert, or_, select, update
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.ext.asyncio import AsyncConnection

from papiq.adapters.outbound.sql import events, tables
from papiq.adapters.outbound.sql.database import Database
from papiq.core.ports.clock import Clock
from papiq.core.ports.event_bus import DEFAULT_DELIVERY_RETRY, DeliveryRetry, EventHandler

log = logging.getLogger(__name__)

_outbox = tables.outbox
_subscriptions = tables.event_subscriptions
_retries = tables.event_retries


@dataclass
class _Subscriber:
    handler: EventHandler
    since: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class _Failure:
    error: str
    retry_at: datetime | None  # None: given up


class SqlEventBus:
    def __init__(
        self,
        database: Database,
        *,
        clock: Clock | None = None,
        retry: DeliveryRetry = DEFAULT_DELIVERY_RETRY,
    ) -> None:
        self._db = database
        self._clock = clock
        self._retry = retry
        self._subscribers: dict[str, _Subscriber] = {}
        self._lock = asyncio.Lock()

    def subscribe(self, subscriber: str, handler: EventHandler) -> None:
        if subscriber in self._subscribers:
            raise ValueError(f"subscriber {subscriber!r} is already registered")
        self._subscribers[subscriber] = _Subscriber(handler)

    async def dispatch(self, *, limit: int = 100) -> int:
        async with self._lock:
            delivered = 0
            for name, subscriber in list(self._subscribers.items()):
                delivered += await self._dispatch_to(name, subscriber, limit)
            return delivered

    async def purge(self, *, before: datetime) -> int:
        lowest = select(func.min(_subscriptions.c.position)).scalar_subquery()
        everything = select(func.coalesce(func.max(_outbox.c.seq), 0)).scalar_subquery()
        async with self._db.writing() as connection:
            result = await connection.execute(
                delete(_outbox).where(
                    _outbox.c.seq <= func.coalesce(lowest, everything),
                    _outbox.c.recorded_at < before,
                    ~exists().where(_retries.c.seq == _outbox.c.seq),
                )
            )
        purged = int(result.rowcount)
        if purged:
            log.info("delivered events purged", extra={"count": purged})
        return purged

    async def _dispatch_to(self, name: str, subscriber: _Subscriber, limit: int) -> int:
        position = await self._position(name, subscriber.since)
        now = self._now()
        async with self._db.reading() as connection:
            retries = (
                await connection.execute(
                    select(_outbox, _retries.c.attempts)
                    .join(_retries, _retries.c.seq == _outbox.c.seq)
                    .where(
                        _retries.c.subscriber == name,
                        or_(_retries.c.retry_at.is_(None), _retries.c.retry_at <= now),
                    )
                    .order_by(_retries.c.attempts, _outbox.c.seq)
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

        retried, retries_failed, retries_delivered = await self._deliver(
            name, subscriber.handler, retries, {row.seq: row.attempts for row in retries}
        )
        _, fresh_failed, fresh_delivered = await self._deliver(name, subscriber.handler, fresh, {})
        delivered = retries_delivered + fresh_delivered

        async with self._db.writing() as connection:
            if fresh:
                moved = await connection.execute(
                    update(_subscriptions)
                    .where(_subscriptions.c.name == name, _subscriptions.c.position == position)
                    .values(position=fresh[-1].seq)
                )
                if moved.rowcount == 0:
                    log.warning(
                        "subscription moved by another dispatcher", extra={"subscriber": name}
                    )
                    return delivered
                waiting = {
                    seq: failure
                    for seq, failure in fresh_failed.items()
                    if failure.retry_at is not None
                }
                if waiting:
                    await connection.execute(
                        insert(_retries),
                        [
                            {
                                "subscriber": name,
                                "seq": seq,
                                "attempts": 1,
                                "last_error": failure.error,
                                "retry_at": failure.retry_at,
                            }
                            for seq, failure in waiting.items()
                        ],
                    )
            given_up = [seq for seq, failure in retries_failed.items() if failure.retry_at is None]
            if retried or given_up:
                await connection.execute(
                    delete(_retries).where(
                        _retries.c.subscriber == name, _retries.c.seq.in_([*retried, *given_up])
                    )
                )
            for seq, failure in retries_failed.items():
                if failure.retry_at is None:
                    continue
                await connection.execute(
                    update(_retries)
                    .where(_retries.c.subscriber == name, _retries.c.seq == seq)
                    .values(
                        attempts=_retries.c.attempts + 1,
                        last_error=failure.error,
                        retry_at=failure.retry_at,
                    )
                )
        return delivered

    async def _deliver(
        self,
        name: str,
        handler: EventHandler,
        rows: Sequence[Row[Any]],
        attempts: dict[int, int],
    ) -> tuple[list[int], dict[int, _Failure], int]:
        """Call the handler for each row; `attempts` holds the failed attempts so far. Returns
        the handled `seq`s (delivered or skipped), the failed ones, and the number of successful
        deliveries."""
        handled: list[int] = []
        failed: dict[int, _Failure] = {}
        delivered = 0
        for row in rows:
            try:
                event = events.decode(row.type, row.event_id, row.occurred_at, row.payload)
            except (KeyError, TypeError, ValueError):
                log.exception(
                    "undecodable event skipped",
                    extra={"subscriber": name, "event_id": str(row.event_id), "type": row.type},
                )
                handled.append(row.seq)
                continue
            try:
                await handler(event)
            except Exception as error:
                tries = attempts.get(row.seq, 0) + 1
                retry_at = self._retry.next_attempt(tries, self._now())
                extra = {"subscriber": name, "event_id": str(row.event_id), "attempts": tries}
                if retry_at is None:
                    log.exception("event delivery given up", extra=extra)
                else:
                    log.warning("event handler failed", extra=extra, exc_info=True)
                failed[row.seq] = _Failure(repr(error), retry_at)
            else:
                handled.append(row.seq)
                delivered += 1
        return handled, failed, delivered

    def _now(self) -> datetime:
        return self._clock.now() if self._clock is not None else datetime.now(UTC)

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
        log.info("event subscription created", extra={"subscriber": name, "position": position})
        return position


async def _read_position(connection: AsyncConnection, name: str) -> int | None:
    row = (
        await connection.execute(
            select(_subscriptions.c.position).where(_subscriptions.c.name == name)
        )
    ).first()
    return None if row is None else int(row.position)
