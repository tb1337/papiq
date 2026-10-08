"""Delivering events to webhooks.

Two steps, both idempotent and safe to repeat (events arrive at least once):

1. **Fan-out** (`on_event`, the event bus subscriber `webhooks.fanout`): for an event, queue one
   `webhooks.deliver` job for each active webhook that wants its type and whose owner may read
   the document. The job's dedup key is `<webhook>:<event>`, so a repeated event adds no second
   job while the first is queued or running. `document.deleted` uses the readers stored in the
   event (the document is gone); `document.filed` is skipped while the document has no lane
   (the pipeline's last `filed` comes after the lane is set).
2. **Delivery** (`run_next_job`): each attempt checks the rights again against the current
   state, so a user who lost access in the meantime gets nothing (`dropped`). Then it signs the
   thin body with the webhook's secret(s), sends it, writes one delivery log row and settles the
   job: delivered, repeated later, or given up.

   | answer                                        | result                               |
   | --------------------------------------------- | ------------------------------------ |
   | 2xx                                           | delivered                            |
   | no answer, 5xx, 408, 425, 429                 | repeated, up to `max_attempts`       |
   | anything else (3xx, other 4xx)                | given up at once                     |

   Repetitions wait `retry_delay`, doubling, at most `max_retry_delay`. After `disable_after`
   given-up deliveries in a row the webhook is switched off (`disabled_reason` `failing`).

Secrets are decrypted only for signing; neither they nor the receiver's answer are logged or
stored.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from uuid import UUID

from papiq.core.domain.documents import Document
from papiq.core.domain.errors import ConcurrencyError, NotFoundError
from papiq.core.domain.events import DocumentDeleted, DocumentEvent, DocumentFiled, DomainEvent
from papiq.core.domain.ids import DocumentId, EventId, UserId, WebhookId, new_id
from papiq.core.domain.jobs import Job
from papiq.core.domain.json_value import JsonObject
from papiq.core.domain.permissions import can_read_document
from papiq.core.domain.users import User
from papiq.core.domain.webhooks import (
    TEST_EVENT,
    DeliveryOutcome,
    Webhook,
    WebhookDelivery,
    event_body,
    signed_headers,
)
from papiq.core.ports import (
    Clock,
    DecryptionError,
    DeliveryRetry,
    SecretCipher,
    UnitOfWork,
    UnitOfWorkFactory,
    WebhookRequest,
    WebhookResponse,
    WebhookSender,
)
from papiq.core.services._access import load_actor
from papiq.core.services.webhooks.management import manageable_webhook, secret_context
from papiq.core.services.webhooks.policy import WebhookPolicy

log = logging.getLogger(__name__)

SUBSCRIBER = "webhooks.fanout"
DELIVER_JOB = "webhooks.deliver"
"""Payload: `webhook_id`, `event_id`, `type`, `occurred_at` (ISO 8601) and `document_id`."""

_RETRY_STATUS = frozenset({408, 425, 429})
_RESULT_ROUNDS = 3  # tries to write the result when the webhook is changed at the same time
_LEASE_MARGIN = timedelta(seconds=60)  # on top of the timeout of an attempt


class WebhookDeliveryService:
    def __init__(
        self,
        uow: UnitOfWorkFactory,
        clock: Clock,
        cipher: SecretCipher,
        sender: WebhookSender,
        policy: WebhookPolicy | None = None,
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._cipher = cipher
        self._sender = sender
        self._policy = policy or WebhookPolicy()
        self._retry = DeliveryRetry(
            max_attempts=self._policy.max_attempts,
            delay=self._policy.retry_delay,
            max_delay=self._policy.max_retry_delay,
        )

    # --- fan-out --------------------------------------------------------------------------------

    async def on_event(self, event: DomainEvent) -> None:
        """The event bus subscriber: queue a delivery per matching webhook."""
        if not isinstance(event, DocumentEvent):
            return
        async with self._uow() as uow:
            webhooks = await uow.webhooks.list_active_for(event.type)
            if not webhooks:
                return
            owners = await _owners_who_may_read(uow, event, {w.owner_id for w in webhooks})
            queued = 0
            for webhook in webhooks:
                if webhook.owner_id not in owners:
                    continue
                payload: JsonObject = {
                    "webhook_id": str(webhook.id),
                    "event_id": str(event.id),
                    "type": event.type,
                    "occurred_at": event.occurred_at.isoformat(),
                    "document_id": str(event.document_id),
                }
                job = await uow.jobs.enqueue(
                    DELIVER_JOB,
                    payload,
                    run_at=self._clock.now(),
                    dedup_key=f"{webhook.id}:{event.id}",
                )
                queued += job is not None
            await uow.commit()
        if queued:
            log.debug("webhook deliveries queued", extra={"event": event.type, "count": queued})

    # --- delivery -------------------------------------------------------------------------------

    async def run_next_job(self) -> bool:
        """Claim and run a due delivery. Returns False if none was due."""
        async with self._uow() as uow:
            job = await uow.jobs.claim(
                now=self._clock.now(),
                lease=self._policy.timeout + _LEASE_MARGIN,
                kinds=[DELIVER_JOB],
            )
            await uow.commit()
        if job is None:
            return False
        try:
            await self._run_claimed(job)
        except asyncio.CancelledError:
            await asyncio.shield(self._release(job))
            raise
        return True

    async def send_test(self, actor: UserId, id: WebhookId) -> WebhookDelivery:
        """Owner or admin: send a `webhook.test` request now, once, and return the log row. Works
        for a switched-off webhook too; does not count towards switching it off."""
        async with self._uow() as uow:
            webhook = await manageable_webhook(uow, await load_actor(uow, actor), id)
        event_id = EventId(new_id())
        now = self._clock.now()
        secrets = self._decrypt(webhook, now)
        response = await self._send(webhook, secrets, event_id, TEST_EVENT, now, None)
        outcome = DeliveryOutcome.DELIVERED if _is_success(response) else DeliveryOutcome.GAVE_UP
        delivery = _row(webhook, event_id, TEST_EVENT, None, 1, now, response, outcome)
        async with self._uow() as uow:
            await uow.webhooks.add_delivery(delivery)
            await uow.commit()
        return delivery

    async def _run_claimed(self, job: Job) -> None:
        try:
            webhook_id = WebhookId(UUID(str(job.payload["webhook_id"])))
            event_id = EventId(UUID(str(job.payload["event_id"])))
            event_type = str(job.payload["type"])
            occurred_at = datetime.fromisoformat(str(job.payload["occurred_at"]))
            raw_document = job.payload.get("document_id")
            document_id = None if raw_document is None else DocumentId(UUID(str(raw_document)))
        except (KeyError, TypeError, ValueError) as error:
            log.error("invalid webhook job", extra={"job_id": str(job.id), "error": str(error)})
            await self._settle(job, fail=f"invalid payload: {error}")
            return
        try:
            await self._attempt(job, webhook_id, event_id, event_type, occurred_at, document_id)
        except ConcurrencyError:
            log.warning("lost the claim of a webhook job", extra={"job_id": str(job.id)})
        except Exception as error:
            await self._failed(job, error)

    async def _attempt(
        self,
        job: Job,
        webhook_id: WebhookId,
        event_id: EventId,
        event_type: str,
        occurred_at: datetime,
        document_id: DocumentId | None,
    ) -> None:
        now = self._clock.now()
        async with self._uow() as uow:
            webhook = await uow.webhooks.find(webhook_id)
            if webhook is None:
                # Deleted since: nothing to log it in.
                await uow.jobs.complete(job)
                await uow.commit()
                return
            reason = await _reason_to_drop(uow, webhook, event_type, document_id)
        attempt = job.tries
        if reason is not None:
            response = WebhookResponse(status_code=None, duration_ms=0, error=reason)
            await self._settle_attempt(
                job,
                webhook_id,
                event_id,
                event_type,
                document_id,
                attempt,
                now,
                response,
                DeliveryOutcome.DROPPED,
            )
            return
        try:
            secrets = self._decrypt(webhook, now)
            response = await self._send(
                webhook, secrets, event_id, event_type, occurred_at, document_id
            )
        except (DecryptionError, ValueError):
            log.error("webhook secret unusable", extra={"webhook_id": str(webhook_id)})
            response = WebhookResponse(
                status_code=None,
                duration_ms=0,
                error="the secret cannot be used (was the secret key changed?)",
            )
            outcome = DeliveryOutcome.GAVE_UP
        else:
            outcome = self._outcome(response, attempt)
        await self._settle_attempt(
            job, webhook_id, event_id, event_type, document_id, attempt, now, response, outcome
        )

    def _outcome(self, response: WebhookResponse, attempt: int) -> DeliveryOutcome:
        if _is_success(response):
            return DeliveryOutcome.DELIVERED
        if _is_temporary(response) and attempt < self._policy.max_attempts:
            return DeliveryOutcome.RETRYING
        return DeliveryOutcome.GAVE_UP

    async def _send(
        self,
        webhook: Webhook,
        secrets: list[str],
        event_id: EventId,
        event_type: str,
        occurred_at: datetime,
        document_id: DocumentId | None,
    ) -> WebhookResponse:
        body = event_body(
            event_id=event_id, type=event_type, occurred_at=occurred_at, document_id=document_id
        )
        headers = {
            "Content-Type": "application/json",
            **signed_headers(secrets, str(event_id), int(self._clock.now().timestamp()), body),
        }
        return await self._sender.send(
            WebhookRequest(
                url=webhook.url, headers=headers, body=body, timeout=self._policy.timeout
            )
        )

    def _decrypt(self, webhook: Webhook, now: datetime) -> list[str]:
        context = secret_context(webhook.id)
        return [
            self._cipher.decrypt(encrypted, context=context).decode("ascii")
            for encrypted in webhook.encrypted_secrets(now)
        ]

    # --- result ---------------------------------------------------------------------------------

    async def _settle_attempt(
        self,
        job: Job,
        webhook_id: WebhookId,
        event_id: EventId,
        event_type: str,
        document_id: DocumentId | None,
        attempt: int,
        started: datetime,
        response: WebhookResponse,
        outcome: DeliveryOutcome,
    ) -> None:
        """One transaction: log row, counter of the webhook, job. If the webhook is changed at
        the same moment, read it again and retry."""
        for round_ in range(_RESULT_ROUNDS):
            now = self._clock.now()
            next_attempt = (
                self._retry.next_attempt(attempt, now)
                if outcome is DeliveryOutcome.RETRYING
                else None
            )
            switched_off: bool | None = None
            try:
                async with self._uow() as uow:
                    webhook = await uow.webhooks.find(webhook_id)
                    if webhook is not None:
                        delivery = _row(
                            webhook,
                            event_id,
                            event_type,
                            document_id,
                            attempt,
                            started,
                            response,
                            outcome,
                            next_attempt,
                        )
                        await uow.webhooks.add_delivery(delivery)
                        switched_off = self._count(webhook, outcome, now)
                        if switched_off is not None:
                            await uow.webhooks.update(webhook)
                    if next_attempt is not None:
                        await uow.jobs.reschedule(
                            job, run_at=next_attempt, error=response.error or _status_text(response)
                        )
                    else:
                        await uow.jobs.complete(job)
                    await uow.commit()
            except ConcurrencyError:
                if round_ + 1 == _RESULT_ROUNDS:
                    raise
                continue
            if webhook is not None and switched_off:
                log.warning(
                    "webhook switched off after repeated failures",
                    extra={"webhook_id": str(webhook_id), "owner_id": str(webhook.owner_id)},
                )
            return

    def _count(self, webhook: Webhook, outcome: DeliveryOutcome, now: datetime) -> bool | None:
        """Update the failure counter. None: nothing changed; else whether it switched the
        webhook off."""
        match outcome:
            case DeliveryOutcome.DELIVERED:
                if webhook.failed_streak == 0:
                    return None
                webhook.delivered()
                return False
            case DeliveryOutcome.GAVE_UP:
                return webhook.gave_up(disable_after=self._policy.disable_after, now=now)
            case _:
                return None

    async def _failed(self, job: Job, error: Exception) -> None:
        """An unexpected error (database, ...): repeat the job like a failed attempt."""
        reason = f"{type(error).__name__}: {error}"
        log.warning("webhook job failed", extra={"job_id": str(job.id)}, exc_info=True)
        next_attempt = self._retry.next_attempt(job.tries, self._clock.now())
        if next_attempt is None:
            log.error("webhook job given up", extra={"job_id": str(job.id), "error": reason})
            await self._settle(job, fail=reason)
            return
        try:
            async with self._uow() as uow:
                await uow.jobs.reschedule(job, run_at=next_attempt, error=reason)
                await uow.commit()
        except ConcurrencyError:
            log.warning("lost the claim of a webhook job", extra={"job_id": str(job.id)})

    async def _settle(self, job: Job, *, fail: str) -> None:
        try:
            async with self._uow() as uow:
                await uow.jobs.fail(job, error=fail)
                await uow.commit()
        except ConcurrencyError:
            log.warning("lost the claim of a webhook job", extra={"job_id": str(job.id)})

    async def _release(self, job: Job) -> None:
        """The worker stops: give the job back to run again at once."""
        try:
            async with self._uow() as uow:
                await uow.jobs.release(job, run_at=self._clock.now(), error="worker stopped")
                await uow.commit()
        except ConcurrencyError:
            log.warning("lost the claim of a webhook job", extra={"job_id": str(job.id)})


# --- rights -------------------------------------------------------------------------------------


async def _owners_who_may_read(
    uow: UnitOfWork, event: DocumentEvent, owners: set[UserId]
) -> set[UserId]:
    """Those of `owners` who are to hear of the event."""
    if isinstance(event, DocumentDeleted):
        return owners & set(event.readers)
    document = await uow.documents.find(event.document_id)
    if document is None:
        return set()
    if isinstance(event, DocumentFiled) and document.lane is None:
        return set()
    drawer = await uow.drawers.get(document.drawer_id)
    allowed: set[UserId] = set()
    for owner in owners:
        user = await uow.users.find(owner)
        if user is not None and can_read_document(user, document, drawer):
            allowed.add(owner)
    return allowed


DOCUMENT_UNAVAILABLE = "the document is not available to the owner"


async def _reason_to_drop(
    uow: UnitOfWork, webhook: Webhook, event_type: str, document_id: DocumentId | None
) -> str | None:
    """Why the request must not be sent now; None if it may."""
    if not webhook.active:
        return "the webhook is switched off"
    owner = await uow.users.find(webhook.owner_id)
    if owner is None or not owner.active:
        return "the owner's account is not active"
    if event_type == DocumentDeleted.type or document_id is None:
        return None  # the readers were fixed with the event
    document = await uow.documents.find(document_id)
    if document is None:
        return DOCUMENT_UNAVAILABLE
    if not await _may_read(uow, owner, document):
        return DOCUMENT_UNAVAILABLE  # the same words: gone and hidden must not be told apart
    return None


async def _may_read(uow: UnitOfWork, user: User, document: Document) -> bool:
    try:
        drawer = await uow.drawers.get(document.drawer_id)
    except NotFoundError:
        return False
    return can_read_document(user, document, drawer)


# --- answers ------------------------------------------------------------------------------------


def _is_success(response: WebhookResponse) -> bool:
    return response.status_code is not None and 200 <= response.status_code < 300


def _is_temporary(response: WebhookResponse) -> bool:
    status = response.status_code
    return status is None or status >= 500 or status in _RETRY_STATUS


def _status_text(response: WebhookResponse) -> str:
    return f"HTTP {response.status_code}"


def _row(
    webhook: Webhook,
    event_id: EventId,
    event_type: str,
    document_id: DocumentId | None,
    attempt: int,
    started: datetime,
    response: WebhookResponse,
    outcome: DeliveryOutcome,
    next_attempt: datetime | None = None,
) -> WebhookDelivery:
    return WebhookDelivery.record(
        webhook_id=webhook.id,
        event_id=event_id,
        event_type=event_type,
        document_id=document_id,
        attempt=attempt,
        started_at=started,
        duration_ms=response.duration_ms,
        outcome=outcome,
        status_code=response.status_code,
        error=response.error,
        next_attempt_at=next_attempt,
    )
