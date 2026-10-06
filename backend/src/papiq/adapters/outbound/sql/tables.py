"""Table definitions. The domain classes are not mapped; `mapping` converts rows explicitly.

Names that are unique regardless of case are stored twice: as entered, and as `name_key`
(`papiq.core.domain.validation.name_key`, Unicode case folding) with a unique index. The key is
computed in Python, so "Ärzte" and "ärzte" collide on SQLite and Postgres alike, independent of
collations.

The migrations in `migrations/versions` must create exactly these tables; a test compares them.
"""

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    Double,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    Uuid,
)

from papiq.adapters.outbound.sql.types import ExactDecimal, UtcDateTime, json_type

metadata = MetaData(
    naming_convention={
        "ix": "ix_%(table_name)s_%(column_0_N_name)s",
        "uq": "uq_%(table_name)s_%(column_0_N_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
)

# Auto-increment primary keys. On SQLite, BIGINT would not become an alias of the rowid.
_SEQUENCE = BigInteger().with_variant(Integer(), "sqlite")

# Job statuses that block another job with the same dedup key (`JobStatus.is_active`).
ACTIVE_JOB_STATUSES = ("queued", "running")

users = Table(
    "users",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("username", Text, nullable=False),
    Column("username_key", Text, nullable=False, unique=True),
    Column("role", Text, nullable=False),
    Column("created_at", UtcDateTime, nullable=False),
    Column("version", Integer, nullable=False),
)

drawers = Table(
    "drawers",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("owner_id", Uuid, ForeignKey("users.id"), nullable=False),
    Column("name", Text, nullable=False),
    Column("name_key", Text, nullable=False),
    Column("is_default", Boolean, nullable=False),
    Column("created_at", UtcDateTime, nullable=False),
    Column("version", Integer, nullable=False),
    Index(None, "owner_id", "name_key", unique=True),
)
# One default drawer per owner.
Index(
    "uq_drawers_default_owner_id",
    drawers.c.owner_id,
    unique=True,
    sqlite_where=drawers.c.is_default,
    postgresql_where=drawers.c.is_default,
)

drawer_shares = Table(
    "drawer_shares",
    metadata,
    Column("drawer_id", Uuid, ForeignKey("drawers.id", ondelete="CASCADE"), primary_key=True),
    Column("user_id", Uuid, ForeignKey("users.id"), primary_key=True, index=True),
    Column("level", Text, nullable=False),
)


def _master_data(name: str) -> Table:
    return Table(
        name,
        metadata,
        Column("id", Uuid, primary_key=True),
        Column("name", Text, nullable=False),
        Column("name_key", Text, nullable=False, unique=True),
        Column("created_at", UtcDateTime, nullable=False),
        Column("version", Integer, nullable=False),
    )


contacts = _master_data("contacts")
document_types = _master_data("document_types")
tags = _master_data("tags")

attribute_definitions = Table(
    "attribute_definitions",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("name", Text, nullable=False),
    Column("name_key", Text, nullable=False, unique=True),
    Column("data_type", Text, nullable=False),
    # False: the attribute applies to the types in `attribute_document_types` only (maybe none).
    Column("is_global", Boolean, nullable=False),
    Column("choices", json_type(), nullable=False),
    Column("created_at", UtcDateTime, nullable=False),
    Column("version", Integer, nullable=False),
)

attribute_document_types = Table(
    "attribute_document_types",
    metadata,
    Column(
        "attribute_id",
        Uuid,
        ForeignKey("attribute_definitions.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("document_type_id", Uuid, ForeignKey("document_types.id"), primary_key=True),
)

documents = Table(
    "documents",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("owner_id", Uuid, ForeignKey("users.id"), nullable=False),
    Column("drawer_id", Uuid, ForeignKey("drawers.id"), nullable=False, index=True),
    Column("sha256", String(64), nullable=False),
    Column("title", Text, nullable=False),
    Column("original_filename", Text, nullable=False),
    Column("media_type", Text, nullable=False),
    Column("contact_id", Uuid, ForeignKey("contacts.id"), nullable=True),
    Column("document_type_id", Uuid, ForeignKey("document_types.id"), nullable=True),
    Column("document_date", Date, nullable=True),
    Column("lane", Text, nullable=True),
    Column("processing_status", Text, nullable=False),
    Column("processing_step", Text, nullable=True),
    Column("processing_run", Integer, nullable=False),
    # Outcomes of the current run: {step: outcome}.
    Column("processing_outcomes", json_type(), nullable=False),
    Column("created_at", UtcDateTime, nullable=False),
    Column("updated_at", UtcDateTime, nullable=False),
    Column("version", Integer, nullable=False),
    # The original is unique per owner, also against concurrent uploads.
    Index(None, "owner_id", "sha256", unique=True),
)

document_tags = Table(
    "document_tags",
    metadata,
    Column("document_id", Uuid, ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", Uuid, ForeignKey("tags.id"), primary_key=True),
)

# One typed column per kind of value; `kind` says which one is set and how to read it.
document_attributes = Table(
    "document_attributes",
    metadata,
    Column("document_id", Uuid, ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True),
    Column("attribute_id", Uuid, ForeignKey("attribute_definitions.id"), primary_key=True),
    Column("kind", Text, nullable=False),  # text, url, decimal, money, date, boolean
    Column("value_text", Text, nullable=True),
    Column("value_decimal", ExactDecimal, nullable=True),
    Column("value_currency", String(3), nullable=True),
    Column("value_date", Date, nullable=True),
    Column("value_boolean", Boolean, nullable=True),
)

processing_log = Table(
    "processing_log",
    metadata,
    Column("seq", _SEQUENCE, primary_key=True, autoincrement=True),
    Column(
        "document_id",
        Uuid,
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column("step", Text, nullable=False),
    Column("run", Integer, nullable=False),
    Column("outcome", Text, nullable=False),
    Column("reason", Text, nullable=True),
    Column("confidence", Double, nullable=True),
    Column("model_version", Text, nullable=True),
    Column("input", json_type(), nullable=False),
    Column("output", json_type(), nullable=False),
    Column("pipeline_version", Text, nullable=False),
    Column("started_at", UtcDateTime, nullable=False),
    Column("duration_us", BigInteger, nullable=False),
)

jobs = Table(
    "jobs",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("kind", Text, nullable=False),
    Column("payload", json_type(), nullable=False),
    Column("dedup_key", Text, nullable=True),
    Column("status", Text, nullable=False),
    Column("attempts", Integer, nullable=False),
    Column("run_at", UtcDateTime, nullable=False),
    Column("locked_until", UtcDateTime, nullable=True),
    Column("last_error", Text, nullable=True),
    Index(None, "status", "run_at"),
)
# At most one queued or running job per dedup key.
Index(
    "uq_jobs_active_dedup_key",
    jobs.c.dedup_key,
    unique=True,
    sqlite_where=jobs.c.status.in_(ACTIVE_JOB_STATUSES),
    postgresql_where=jobs.c.status.in_(ACTIVE_JOB_STATUSES),
)

# Committed domain events. `seq` follows commit order (see `unit_of_work`); on SQLite,
# AUTOINCREMENT keeps it from reusing the numbers of deleted rows.
outbox = Table(
    "outbox",
    metadata,
    Column("seq", _SEQUENCE, primary_key=True, autoincrement=True),
    Column("event_id", Uuid, nullable=False, unique=True),
    Column("type", Text, nullable=False),
    Column("occurred_at", UtcDateTime, nullable=False),
    Column("payload", json_type(), nullable=False),
    # When the event was written (system time); places new subscriptions, see `event_bus`.
    Column("recorded_at", UtcDateTime, nullable=False),
    sqlite_autoincrement=True,
)

# Durable subscriptions: every event with `seq <= position` has been handled; failed ones wait
# in `event_retries`.
event_subscriptions = Table(
    "event_subscriptions",
    metadata,
    Column("name", Text, primary_key=True),
    Column("position", BigInteger, nullable=False),
    Column("created_at", UtcDateTime, nullable=False),
)

event_retries = Table(
    "event_retries",
    metadata,
    Column(
        "subscriber",
        Text,
        ForeignKey("event_subscriptions.name", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("seq", _SEQUENCE, ForeignKey("outbox.seq", ondelete="CASCADE"), primary_key=True),
    Column("attempts", Integer, nullable=False),
    Column("last_error", Text, nullable=False),
)
