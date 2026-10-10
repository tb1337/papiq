"""Identifiers: UUIDv7 (time-ordered), one distinct type per entity."""

import os
import time
from typing import NewType
from uuid import UUID

UserId = NewType("UserId", UUID)
DrawerId = NewType("DrawerId", UUID)
DocumentId = NewType("DocumentId", UUID)
ContactId = NewType("ContactId", UUID)
DocumentTypeId = NewType("DocumentTypeId", UUID)
TagId = NewType("TagId", UUID)
FieldId = NewType("FieldId", UUID)
EventId = NewType("EventId", UUID)
JobId = NewType("JobId", UUID)
SessionId = NewType("SessionId", UUID)
ApiTokenId = NewType("ApiTokenId", UUID)
ExternalIdentityId = NewType("ExternalIdentityId", UUID)
RuleId = NewType("RuleId", UUID)
RuleApplicationId = NewType("RuleApplicationId", UUID)
WebhookId = NewType("WebhookId", UUID)
DeliveryId = NewType("DeliveryId", UUID)

_TIMESTAMP_BITS = 48
_RANDOM_BITS = 80


def new_id() -> UUID:
    """A new UUIDv7 (RFC 9562): Unix time in milliseconds, then random bits.

    IDs sort by creation time to the millisecond; within a millisecond the order is random.
    """
    milliseconds = time.time_ns() // 1_000_000 & ((1 << _TIMESTAMP_BITS) - 1)
    random = int.from_bytes(os.urandom(_RANDOM_BITS // 8))
    value = milliseconds << _RANDOM_BITS | random
    value = value & ~(0xF << 76) | 0x7 << 76  # version 7
    value = value & ~(0x3 << 62) | 0x2 << 62  # variant RFC 9562
    return UUID(int=value)
