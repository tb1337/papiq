"""Creating, changing and deleting webhooks, and reading their delivery log.

Who may do what: a webhook belongs to its owner, who changes and deletes it, renews its secret
and tests it. Admins read every webhook and its log (never the secret, which is shown once).
Other users do not learn that someone else's webhook exists (NotFoundError).
"""

import builtins
from collections.abc import Collection
from dataclasses import dataclass

from papiq.core.domain.errors import ConflictError, NotFoundError, PermissionDeniedError
from papiq.core.domain.ids import DeliveryId, UserId, WebhookId
from papiq.core.domain.users import User
from papiq.core.domain.webhooks import Webhook, WebhookDelivery, new_secret
from papiq.core.ports import Clock, SecretCipher, UnitOfWork, UnitOfWorkFactory
from papiq.core.services._access import load_actor
from papiq.core.services.webhooks.policy import WebhookPolicy

MAX_PAGE = 200


@dataclass(frozen=True)
class CreatedWebhook:
    """A new or renewed secret is shown here and nowhere else."""

    webhook: Webhook
    secret: str


def secret_context(id: WebhookId) -> bytes:
    """Binds an encrypted secret to its webhook."""
    return b"webhook:" + id.bytes


class WebhookService:
    def __init__(
        self,
        uow: UnitOfWorkFactory,
        clock: Clock,
        cipher: SecretCipher,
        policy: WebhookPolicy | None = None,
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._cipher = cipher
        self._policy = policy or WebhookPolicy()

    async def create(
        self,
        actor: UserId,
        *,
        name: str,
        url: str,
        event_types: Collection[str],
        active: bool = True,
    ) -> CreatedWebhook:
        """ValidationError for a bad URL or event type, ConflictError at the user's limit."""
        async with self._uow() as uow:
            user = await load_actor(uow, actor)
            if await uow.webhooks.count_for_owner(user.id) >= self._policy.per_user:
                raise ConflictError(f"at most {self._policy.per_user} webhooks per user")
            secret = new_secret()
            webhook = Webhook.create(
                owner_id=user.id,
                name=name,
                url=url,
                event_types=event_types,
                encrypted_secret=b"",
                now=self._clock.now(),
                active=active,
            )
            webhook.encrypted_secret = self._encrypt(webhook, secret)
            await uow.webhooks.add(webhook)
            await uow.commit()
        return CreatedWebhook(webhook, secret)

    async def list(self, actor: UserId, *, owner: UserId | None = None) -> "builtins.list[Webhook]":
        """The caller's webhooks, oldest first. Admins get everybody's; `owner` narrows to one
        user's."""
        async with self._uow() as uow:
            user = await load_actor(uow, actor)
            if not user.is_active_admin:
                if owner is not None and owner != user.id:
                    return []
                return await uow.webhooks.list_for_owner(user.id)
            if owner is not None:
                return await uow.webhooks.list_for_owner(owner)
            return await uow.webhooks.list_all()

    async def get(self, actor: UserId, id: WebhookId) -> Webhook:
        async with self._uow() as uow:
            return await _visible(uow, await load_actor(uow, actor), id)

    async def update(
        self,
        actor: UserId,
        id: WebhookId,
        *,
        name: str | None = None,
        url: str | None = None,
        event_types: Collection[str] | None = None,
        active: bool | None = None,
    ) -> Webhook:
        async with self._uow() as uow:
            webhook = await _owned(uow, await load_actor(uow, actor), id)
            webhook.change(
                now=self._clock.now(), name=name, url=url, event_types=event_types, active=active
            )
            await uow.webhooks.update(webhook)
            await uow.commit()
            return webhook

    async def delete(self, actor: UserId, id: WebhookId) -> None:
        """The webhook and its log. Deliveries that are queued find it gone and stop."""
        async with self._uow() as uow:
            webhook = await _owned(uow, await load_actor(uow, actor), id)
            await uow.webhooks.remove(webhook.id)
            await uow.commit()

    async def renew_secret(self, actor: UserId, id: WebhookId) -> CreatedWebhook:
        """A new secret, shown once. The old one signs next to it for the grace period."""
        async with self._uow() as uow:
            webhook = await _owned(uow, await load_actor(uow, actor), id)
            secret = new_secret()
            webhook.renew_secret(
                self._encrypt(webhook, secret),
                now=self._clock.now(),
                grace=self._policy.secret_grace,
            )
            await uow.webhooks.update(webhook)
            await uow.commit()
        return CreatedWebhook(webhook, secret)

    async def deliveries(
        self, actor: UserId, id: WebhookId, *, before: DeliveryId | None = None, limit: int = 50
    ) -> "builtins.list[WebhookDelivery]":
        """The log of a webhook, newest first."""
        limit = max(1, min(limit, MAX_PAGE))
        async with self._uow() as uow:
            webhook = await _visible(uow, await load_actor(uow, actor), id)
            return await uow.webhooks.deliveries(webhook.id, before=before, limit=limit)

    def _encrypt(self, webhook: Webhook, secret: str) -> bytes:
        return self._cipher.encrypt(secret.encode("ascii"), context=secret_context(webhook.id))


async def _visible(uow: UnitOfWork, user: User, id: WebhookId) -> Webhook:
    webhook = await uow.webhooks.find(id)
    if webhook is None or not (webhook.owner_id == user.id or user.is_active_admin):
        raise NotFoundError("webhook", id)
    return webhook


async def _owned(uow: UnitOfWork, user: User, id: WebhookId) -> Webhook:
    webhook = await _visible(uow, user, id)
    if webhook.owner_id != user.id:
        raise PermissionDeniedError(f"only the owner changes webhook {id}")
    return webhook
