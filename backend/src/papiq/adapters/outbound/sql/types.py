"""Column types that behave the same on SQLite and Postgres.

SQLite knows neither time zones nor exact decimals: timestamps are stored as UTC text of fixed
width (sorts and compares like the instant), decimals as their exact text representation.
"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, DateTime, Dialect, Numeric, Text, TypeDecorator
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import TypeEngine


class UtcDateTime(TypeDecorator[datetime]):
    """Timezone-aware UTC timestamps. Postgres: `timestamptz`; SQLite: UTC text without offset.

    Naive values are rejected; values are always read back as aware UTC datetimes.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"naive datetime {value.isoformat()} cannot be stored")
        value = value.astimezone(UTC)
        return value.replace(tzinfo=None) if dialect.name == "sqlite" else value

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class ExactDecimal(TypeDecorator[Decimal]):
    """Exact decimals with their scale. Postgres: unconstrained `numeric`; SQLite: text, because
    SQLite's numeric affinity would turn them into binary floating point.

    Values in exponent notation keep their value but not their notation on Postgres
    (`Decimal("1E+2")` reads back as `Decimal("100")`, which compares equal).
    """

    impl = Numeric
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[Any]:
        if dialect.name == "sqlite":
            return dialect.type_descriptor(Text())
        return dialect.type_descriptor(Numeric(asdecimal=True))

    def process_bind_param(self, value: Decimal | None, dialect: Dialect) -> Any:
        if value is None:
            return None
        return str(value) if dialect.name == "sqlite" else value

    def process_result_value(self, value: Any, dialect: Dialect) -> Decimal | None:
        if value is None:
            return None
        return value if isinstance(value, Decimal) else Decimal(value)


def json_type() -> TypeEngine[Any]:
    """JSON values: `jsonb` on Postgres, text on SQLite."""
    return JSON().with_variant(JSONB(), "postgresql")
