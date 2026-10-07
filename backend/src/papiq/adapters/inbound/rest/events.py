"""Server-sent events: progress of the documents a user may see.

`EventHub` subscribes to the event bus (subscriber `api.sse`, dispatched by the API process)
and hands each document event to the open streams of the users who may read the document at
that moment. Events are pushed only to connected clients; there is no replay, so a client that
reconnects fetches the current state. One API instance is assumed: several instances would
share the subscription and each would see only part of the events (a broker is the way out).

`document.deleted` is not pushed: after deletion nobody can be checked for access (decided
with the webhooks in M8).
"""

import asyncio
import dataclasses
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any

from fastapi.encoders import jsonable_encoder

from papiq.core.domain.events import DocumentDeleted, DocumentEvent, DomainEvent
from papiq.core.domain.ids import DocumentId, UserId
from papiq.core.ports import EventBus
from papiq.core.services.documents import DocumentService

log = logging.getLogger(__name__)

SUBSCRIBER = "api.sse"
QUEUE_SIZE = 100
MAX_BACKOFF = timedelta(seconds=30)

_CLOSED = object()


@dataclasses.dataclass(eq=False)
class Listener:
    user: UserId
    document: DocumentId | None
    queue: asyncio.Queue[object] = dataclasses.field(
        default_factory=lambda: asyncio.Queue(QUEUE_SIZE)
    )

    async def events(
        self,
        still_allowed: Callable[[], Awaitable[bool]] | None = None,
        every: timedelta = timedelta(seconds=30),
    ) -> AsyncIterator[DocumentEvent]:
        """The events for this listener until the hub closes or the client falls behind.

        With `still_allowed`, the stream asks it at least every `every` (also while no event
        comes) and ends once it answers False, e.g. when the session was revoked."""
        loop = asyncio.get_running_loop()
        checked = loop.time()
        while True:
            if still_allowed is None:
                item = await self.queue.get()
            else:
                try:
                    wait = max(0.0, checked + every.total_seconds() - loop.time())
                    item = await asyncio.wait_for(self.queue.get(), timeout=wait)
                except TimeoutError:
                    item = None
                if loop.time() - checked >= every.total_seconds():
                    if not await still_allowed():
                        return
                    checked = loop.time()
                if item is None:
                    continue
            if item is _CLOSED:
                return
            assert isinstance(item, DocumentEvent)
            yield item

    def offer(self, item: object) -> None:
        try:
            self.queue.put_nowait(item)
        except asyncio.QueueFull:
            # Too slow: end the stream, the client reconnects and fetches the state.
            log.warning("event stream fell behind, closing it", extra={"user": str(self.user)})
            self._close_now()

    def _close_now(self) -> None:
        while not self.queue.empty():
            self.queue.get_nowait()
        self.queue.put_nowait(_CLOSED)


class EventHub:
    def __init__(self, documents: DocumentService) -> None:
        self._documents = documents
        self._listeners: set[Listener] = set()
        self._closed = False

    def attach(self, bus: EventBus) -> None:
        bus.subscribe(SUBSCRIBER, self.handle)

    async def handle(self, event: DomainEvent) -> None:
        if not isinstance(event, DocumentEvent) or isinstance(event, DocumentDeleted):
            return
        listeners = [
            listener
            for listener in self._listeners
            if listener.document in (None, event.document_id)
        ]
        if not listeners:
            return
        readers = await self._documents.filter_readers(
            event.document_id, {listener.user for listener in listeners}
        )
        for listener in listeners:
            if listener.user in readers:
                listener.offer(event)

    @asynccontextmanager
    async def listen(
        self, user: UserId, document: DocumentId | None = None
    ) -> AsyncIterator[Listener]:
        listener = Listener(user, document)
        if self._closed:
            listener.offer(_CLOSED)
        self._listeners.add(listener)
        try:
            yield listener
        finally:
            self._listeners.discard(listener)

    def close(self) -> None:
        """End all streams, e.g. when the server shuts down."""
        self._closed = True
        for listener in list(self._listeners):
            listener._close_now()


def message(event: DocumentEvent) -> dict[str, Any]:
    """The JSON payload of an event."""
    fields = {field.name: getattr(event, field.name) for field in dataclasses.fields(event)}
    payload: dict[str, Any] = jsonable_encoder({"type": event.type, **fields})
    return payload


async def dispatch_forever(bus: EventBus, interval: timedelta) -> None:
    """Deliver outbox events to this process's subscribers until cancelled."""
    failures = 0
    while True:
        try:
            delivered = await bus.dispatch()
        except Exception:
            failures += 1
            log.exception("event dispatch failed", extra={"failures": failures})
            await asyncio.sleep(
                min(interval * (1 << min(failures, 10)), MAX_BACKOFF).total_seconds()
            )
            continue
        failures = 0
        if delivered == 0:
            await asyncio.sleep(interval.total_seconds())
