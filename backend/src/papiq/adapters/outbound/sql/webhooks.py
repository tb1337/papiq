"""Webhook repository on SQL tables. Deliveries go with their webhook (ON DELETE CASCADE)."""

from datetime import datetime
from typing import Any

from sqlalchemy import Row, delete, func, insert, select

from papiq.adapters.outbound.sql import tables as t
from papiq.adapters.outbound.sql.repositories import SqlRepository, Values
from papiq.core.domain.errors import NotFoundError
from papiq.core.domain.ids import DeliveryId, DocumentId, EventId, UserId, WebhookId
from papiq.core.domain.webhooks import DeliveryOutcome, Webhook, WebhookDelivery


class SqlWebhookRepository(SqlRepository[WebhookId, Webhook]):
    kind = "webhook"
    table = t.webhooks

    async def remove(self, id: WebhookId) -> None:
        await self._tx.write(delete(self.table).where(self.table.c.id == id))

    async def remove_for_owner(self, owner: UserId) -> int:
        result = await self._tx.write(delete(self.table).where(self.table.c.owner_id == owner))
        return int(result.rowcount)

    async def list_for_owner(self, owner: UserId) -> list[Webhook]:
        rows = await self._tx.read(
            select(self.table)
            .where(self.table.c.owner_id == owner)
            .order_by(self.table.c.created_at, self.table.c.id)
        )
        return [self._entity(row) for row in rows]

    async def list_all(self) -> list[Webhook]:
        rows = await self._tx.read(
            select(self.table).order_by(self.table.c.created_at, self.table.c.id)
        )
        return [self._entity(row) for row in rows]

    async def count_for_owner(self, owner: UserId) -> int:
        table = self.table
        result = await self._tx.read(
            select(func.count()).select_from(table).where(table.c.owner_id == owner)
        )
        return int(result.scalar_one())

    async def list_active_for(self, event_type: str) -> list[Webhook]:
        # The types are a JSON list; there are few webhooks, so the match is done here.
        table = self.table
        rows = await self._tx.read(
            select(table).where(table.c.active).order_by(table.c.created_at, table.c.id)
        )
        return [webhook for webhook in map(self._entity, rows) if webhook.wants(event_type)]

    async def add_delivery(self, delivery: WebhookDelivery) -> None:
        exists = await self._tx.read(
            select(self.table.c.id).where(self.table.c.id == delivery.webhook_id)
        )
        if exists.first() is None:
            raise NotFoundError("webhook", delivery.webhook_id)
        await self._tx.write(
            insert(t.webhook_deliveries).values(
                id=delivery.id,
                webhook_id=delivery.webhook_id,
                event_id=delivery.event_id,
                event_type=delivery.event_type,
                document_id=delivery.document_id,
                attempt=delivery.attempt,
                started_at=delivery.started_at,
                duration_ms=delivery.duration_ms,
                outcome=delivery.outcome.value,
                status_code=delivery.status_code,
                error=delivery.error,
                next_attempt_at=delivery.next_attempt_at,
            )
        )

    async def deliveries(
        self, webhook: WebhookId, *, before: DeliveryId | None = None, limit: int = 50
    ) -> list[WebhookDelivery]:
        table = t.webhook_deliveries
        query = select(table).where(table.c.webhook_id == webhook)
        if before is not None:
            query = query.where(table.c.id < before)
        rows = await self._tx.read(query.order_by(table.c.id.desc()).limit(limit))
        return [_delivery(row) for row in rows]

    async def purge_deliveries(self, *, before: datetime) -> int:
        table = t.webhook_deliveries
        result = await self._tx.write(delete(table).where(table.c.started_at < before))
        return int(result.rowcount)

    def _entity(self, row: Row[Any]) -> Webhook:
        return Webhook(
            id=WebhookId(row.id),
            owner_id=UserId(row.owner_id),
            name=row.name,
            url=row.url,
            event_types=frozenset(row.event_types),
            encrypted_secret=bytes(row.secret),
            active=row.active,
            disabled_reason=row.disabled_reason,
            failed_streak=row.failed_streak,
            previous_secret=None if row.previous_secret is None else bytes(row.previous_secret),
            previous_valid_until=row.previous_valid_until,
            created_at=row.created_at,
            updated_at=row.updated_at,
            version=row.version,
        )

    def _values(self, entity: Webhook) -> Values:
        return {
            "id": entity.id,
            "owner_id": entity.owner_id,
            "name": entity.name,
            "url": entity.url,
            "event_types": sorted(entity.event_types),
            "secret": entity.encrypted_secret,
            "previous_secret": entity.previous_secret,
            "previous_valid_until": entity.previous_valid_until,
            "active": entity.active,
            "disabled_reason": entity.disabled_reason,
            "failed_streak": entity.failed_streak,
            "created_at": entity.created_at,
            "updated_at": entity.updated_at,
            "version": entity.version,
        }


def _delivery(row: Row[Any]) -> WebhookDelivery:
    return WebhookDelivery(
        id=DeliveryId(row.id),
        webhook_id=WebhookId(row.webhook_id),
        event_id=EventId(row.event_id),
        event_type=row.event_type,
        document_id=None if row.document_id is None else DocumentId(row.document_id),
        attempt=row.attempt,
        started_at=row.started_at,
        duration_ms=row.duration_ms,
        outcome=DeliveryOutcome(row.outcome),
        status_code=row.status_code,
        error=row.error,
        next_attempt_at=row.next_attempt_at,
    )
