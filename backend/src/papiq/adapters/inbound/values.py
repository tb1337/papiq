"""Conversions of values that more than one inbound adapter (REST, MCP) takes in."""

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from papiq.core.domain.attributes import AttributeDefinition, AttributeType, Money, Url
from papiq.core.domain.errors import ValidationError


def attribute_value(definition: AttributeDefinition, value: Any) -> object:
    """The JSON form of an attribute value as the domain type; None removes the value."""
    if value is None:
        return None
    invalid = ValidationError(
        f"attribute '{definition.name}' ({definition.data_type}) does not accept {value!r:.100}"
    )
    try:
        match definition.data_type:
            case AttributeType.NUMBER if isinstance(value, int | float | str) and not isinstance(
                value, bool
            ):
                return Decimal(str(value))
            case AttributeType.AMOUNT if isinstance(value, dict) and set(value) == {
                "amount",
                "currency",
            }:
                return Money(Decimal(str(value["amount"])), value["currency"])
            case AttributeType.DATE if isinstance(value, str):
                return date.fromisoformat(value)
            case AttributeType.LINK if isinstance(value, str):
                return Url(value)
    except (InvalidOperation, ValueError, TypeError):
        raise invalid from None
    return value  # text, choice, boolean: checked by the domain
