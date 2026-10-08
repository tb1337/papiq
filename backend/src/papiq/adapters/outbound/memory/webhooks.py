"""In-memory webhook repository, on the tables of the in-memory unit of work."""

import copy
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from papiq.adapters.outbound.memory.database import WEBHOOK_DELIVERIES, Table
from papiq.adapters.outbound.memory.rows import _REMOVED, MemoryRepository
from papiq.core.domain.errors import NotFoundError
from papiq.core.domain.ids import DeliveryId, UserId, WebhookId
from papiq.core.domain.webhooks import Webhook, WebhookDelivery

if TYPE_CHECKING:
    from papiq.adapters.outbound.memory.unit_of_work import MemoryUnitOfWork


@dataclass
class DeliveryRow:
    """A logged delivery; `version` only for the unit of work's bookkeeping."""

    id: UUID
    item: WebhookDelivery
    version: int = 1


class MemoryWebhookRepository(MemoryRepository[WebhookId, Webhook]):
    def __init__(self, uow: "MemoryUnitOfWork", table: Table) -> None:
        super().__init__(uow, table)
        self._deliveries = MemoryRepository[UUID, DeliveryRow](uow, WEBHOOK_DELIVERIES)

    async def remove(self, id: WebhookId) -> None:
        self._deliveries._remove_rows(
            [row for row in self._deliveries._all() if row.item.webhook_id == id]
        )
        if self._uow._row(self._table, id) is not _REMOVED:
            self._uow._write(self._table, id, _REMOVED)

    async def remove_for_owner(self, owner: UserId) -> int:
        webhooks = [row for row in self._all() if row.owner_id == owner]
        ids = {row.id for row in webhooks}
        self._deliveries._remove_rows(
            [row for row in self._deliveries._all() if row.item.webhook_id in ids]
        )
        return self._remove_rows(webhooks)

    async def list_for_owner(self, owner: UserId) -> list[Webhook]:
        return self._oldest_first(row for row in self._all() if row.owner_id == owner)

    async def list_all(self) -> list[Webhook]:
        return self._oldest_first(self._all())

    async def count_for_owner(self, owner: UserId) -> int:
        return sum(1 for row in self._all() if row.owner_id == owner)

    async def list_active_for(self, event_type: str) -> list[Webhook]:
        return self._oldest_first(
            row for row in self._all() if row.active and row.wants(event_type)
        )

    async def add_delivery(self, delivery: WebhookDelivery) -> None:
        if self._uow._row(self._table, delivery.webhook_id) is _REMOVED:
            raise NotFoundError("webhook", delivery.webhook_id)
        await self._deliveries.add(DeliveryRow(delivery.id, delivery))

    async def deliveries(
        self, webhook: WebhookId, *, before: DeliveryId | None = None, limit: int = 50
    ) -> list[WebhookDelivery]:
        rows = [
            row.item
            for row in self._deliveries._all()
            if row.item.webhook_id == webhook and (before is None or row.id < before)
        ]
        rows.sort(key=lambda delivery: delivery.id, reverse=True)
        return [copy.deepcopy(row) for row in rows[:limit]]

    async def purge_deliveries(self, *, before: datetime) -> int:
        return self._deliveries._remove_rows(
            [row for row in self._deliveries._all() if row.item.started_at < before]
        )

    def _oldest_first(self, rows: Iterable[Webhook]) -> list[Webhook]:
        found = sorted(rows, key=lambda row: (row.created_at, row.id))
        return [self._copy(row) for row in found]
