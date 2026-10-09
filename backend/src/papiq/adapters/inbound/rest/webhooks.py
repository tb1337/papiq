"""`/webhooks`: a user's subscriptions to document events, and the log of their deliveries."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field

from papiq.adapters.inbound.rest.auth import PROTECTED, CurrentUser
from papiq.adapters.inbound.rest.context import Context
from papiq.adapters.inbound.rest.problems import problem_responses
from papiq.core.domain.ids import DeliveryId, UserId, WebhookId
from papiq.core.domain.webhooks import (
    ALL_EVENTS,
    DeliveryOutcome,
    Webhook,
    WebhookDelivery,
    event_type_names,
)
from papiq.core.services.webhooks import CreatedWebhook

router = APIRouter(prefix="/webhooks", tags=["webhooks"], dependencies=PROTECTED)

WHO = (
    "Its owner, or an admin (who also changes, deletes and tests other users' webhooks and "
    "sees the secret only when renewing it). Other users' webhooks are not found (404)."
)
EVENT_TYPES_HELP = (
    f"Event types: {', '.join(f'`{name}`' for name in event_type_names())}, or `{ALL_EVENTS}` "
    "for all, also future ones."
)


class WebhookCreate(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "name": "n8n",
                    "url": "http://n8n.lan:5678/webhook/papiq",
                    "event_types": ["document.lane_changed", "document.filed"],
                }
            ]
        },
    )

    name: str = Field(min_length=1, max_length=100, description="Shown in lists.")
    url: str = Field(
        max_length=2000,
        description=(
            "Where Papiq sends the events (POST). `http` or `https`; targets in the own "
            "network are fine. Redirects are not followed."
        ),
    )
    event_types: list[str] = Field(min_length=1, description=EVENT_TYPES_HELP)
    active: bool = True


class WebhookPatch(BaseModel):
    """Fields left out stay as they are."""

    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [{"active": False}]})

    name: str | None = Field(default=None, min_length=1, max_length=100)
    url: str | None = Field(default=None, max_length=2000)
    event_types: list[str] | None = Field(default=None, min_length=1, description=EVENT_TYPES_HELP)
    active: bool | None = Field(
        default=None, description="Switching on clears the failure count and the reason."
    )


class WebhookOut(BaseModel):
    id: UUID
    owner_id: UUID
    name: str
    url: str
    event_types: list[str]
    active: bool
    disabled_reason: str | None = Field(
        description="`failing`: Papiq switched the webhook off after repeated failed deliveries."
    )
    failed_streak: int = Field(description="Deliveries given up in a row since the last success.")
    previous_secret_valid_until: datetime | None = Field(
        description=(
            "After renewing the secret: until then requests carry a second signature made with "
            "the old secret."
        )
    )
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, webhook: Webhook) -> "WebhookOut":
        return cls(
            id=webhook.id,
            owner_id=webhook.owner_id,
            name=webhook.name,
            url=webhook.url,
            event_types=sorted(webhook.event_types),
            active=webhook.active,
            disabled_reason=webhook.disabled_reason,
            failed_streak=webhook.failed_streak,
            previous_secret_valid_until=webhook.previous_valid_until,
            created_at=webhook.created_at,
            updated_at=webhook.updated_at,
        )


class WebhookCreated(WebhookOut):
    secret: str = Field(
        repr=False,
        description=(
            "The signing secret (`whsec_…`); shown only now. Receivers verify the "
            "`webhook-signature` header with it."
        ),
        examples=["whsec_<secret shown once>"],
    )

    @classmethod
    def created(cls, created: CreatedWebhook) -> "WebhookCreated":
        return cls(**WebhookOut.of(created.webhook).model_dump(), secret=created.secret)


class DeliveryOut(BaseModel):
    id: UUID
    webhook_id: UUID
    event_id: UUID = Field(description="As `webhook-id` and `id` in the request.")
    event_type: str
    document_id: UUID | None
    attempt: int = Field(description="1 for the first try of this event.")
    started_at: datetime
    duration_ms: int
    outcome: DeliveryOutcome = Field(
        description=(
            "`delivered`: answered with 2xx. `retrying`: failed, another attempt follows at "
            "`next_attempt_at`. `gave_up`: failed for good. `dropped`: not sent, because the "
            "owner may no longer see the document or the webhook is off."
        )
    )
    status_code: int | None = Field(description="The receiver's HTTP status, if it answered.")
    error: str | None = Field(description="Why the attempt failed; the answer body is not kept.")
    next_attempt_at: datetime | None

    @classmethod
    def of(cls, delivery: WebhookDelivery) -> "DeliveryOut":
        return cls(**delivery.__dict__)


@router.get(
    "",
    summary="List webhooks",
    description=(
        "The caller's webhooks, oldest first; without the secret. Admins get everybody's, "
        "`owner` narrows them to one user."
    ),
    response_model=list[WebhookOut],
    responses=problem_responses(401, 422),
)
async def list_webhooks(
    user: CurrentUser,
    context: Context,
    owner: Annotated[UUID | None, Query(description="Only this user's (admins).")] = None,
) -> list[WebhookOut]:
    found = await context.webhooks.list(user, owner=None if owner is None else UserId(owner))
    return [WebhookOut.of(webhook) for webhook in found]


@router.post(
    "",
    status_code=201,
    summary="Create a webhook",
    description=(
        "Papiq sends a signed request to the URL for each event of the chosen types on "
        "documents within the owner's reach: their own, and green ones in drawers they own or "
        "that are shared with them (an admin's webhooks too; the rights of an admin do not "
        "widen them). The answer holds the secret, shown only once. At most 20 webhooks per "
        "user (409)."
    ),
    response_model=WebhookCreated,
    responses=problem_responses(401, 403, 409, 422),
)
async def create_webhook(
    body: WebhookCreate, user: CurrentUser, context: Context
) -> WebhookCreated:
    created = await context.webhooks.create(
        user, name=body.name, url=body.url, event_types=body.event_types, active=body.active
    )
    return WebhookCreated.created(created)


@router.get(
    "/{id}",
    summary="A webhook",
    description=WHO,
    response_model=WebhookOut,
    responses=problem_responses(401, 404, 422),
)
async def get_webhook(id: UUID, user: CurrentUser, context: Context) -> WebhookOut:
    return WebhookOut.of(await context.webhooks.get(user, WebhookId(id)))


@router.patch(
    "/{id}",
    summary="Change or switch a webhook on or off",
    description=WHO,
    response_model=WebhookOut,
    responses=problem_responses(401, 403, 404, 409, 422),
)
async def update_webhook(
    id: UUID, body: WebhookPatch, user: CurrentUser, context: Context
) -> WebhookOut:
    webhook = await context.webhooks.update(
        user,
        WebhookId(id),
        name=body.name,
        url=body.url,
        event_types=body.event_types,
        active=body.active,
    )
    return WebhookOut.of(webhook)


@router.delete(
    "/{id}",
    status_code=204,
    summary="Delete a webhook",
    description="With its log. " + WHO,
    responses=problem_responses(401, 403, 404, 422),
)
async def delete_webhook(id: UUID, user: CurrentUser, context: Context) -> None:
    await context.webhooks.delete(user, WebhookId(id))


@router.post(
    "/{id}/secret",
    summary="Renew the secret",
    description=(
        "A new secret, shown only now. The old one keeps signing next to it for a while "
        "(`previous_secret_valid_until`), so the receiver can switch without a gap; renewing "
        "again ends that at once. " + WHO
    ),
    response_model=WebhookCreated,
    responses=problem_responses(401, 403, 404, 422),
)
async def renew_secret(id: UUID, user: CurrentUser, context: Context) -> WebhookCreated:
    return WebhookCreated.created(await context.webhooks.renew_secret(user, WebhookId(id)))


@router.get(
    "/{id}/deliveries",
    summary="The delivery log of a webhook",
    description=(
        "Every attempt, newest first: time, event, status, duration, error. The answer of the "
        "receiver is not kept. Old entries are removed after the retention period. " + WHO
    ),
    response_model=list[DeliveryOut],
    responses=problem_responses(401, 404, 422),
)
async def list_deliveries(
    id: UUID,
    user: CurrentUser,
    context: Context,
    before: Annotated[
        UUID | None, Query(description="The `id` of the last entry of the previous page.")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[DeliveryOut]:
    found = await context.webhooks.deliveries(
        user,
        WebhookId(id),
        before=None if before is None else DeliveryId(before),
        limit=limit,
    )
    return [DeliveryOut.of(delivery) for delivery in found]


@router.post(
    "/{id}/test",
    summary="Send a test request",
    description=(
        "Sends a `webhook.test` request to the URL now, signed like any other, without "
        "repetition; works for a switched-off webhook too. The answer is the entry of the "
        "delivery log: `delivered` for a 2xx answer, else `gave_up` with status or error. "
        "At most 10 test requests per user and minute; beyond that `429` with `Retry-After`. " + WHO
    ),
    response_model=DeliveryOut,
    responses=problem_responses(401, 403, 404, 422, 429),
)
async def test_webhook(id: UUID, user: CurrentUser, context: Context) -> DeliveryOut:
    return DeliveryOut.of(await context.webhook_delivery.send_test(user, WebhookId(id)))
