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
    LargeBinary,
    MetaData,
    String,
    Table,
    Text,
    Uuid,
    true,
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
    Column("active", Boolean, nullable=False, server_default=true()),
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

field_definitions = Table(
    "field_definitions",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("name", Text, nullable=False),
    Column("name_key", Text, nullable=False, unique=True),
    Column("data_type", Text, nullable=False),
    # False: the field applies to the types in `field_document_types` only (maybe none).
    Column("is_global", Boolean, nullable=False),
    Column("choices", json_type(), nullable=False),
    Column("created_at", UtcDateTime, nullable=False),
    Column("version", Integer, nullable=False),
)

field_document_types = Table(
    "field_document_types",
    metadata,
    Column(
        "field_id",
        Uuid,
        ForeignKey("field_definitions.id", ondelete="CASCADE"),
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
    # Intake channel (`Channel`); documents from before M7 count as `api`.
    Column("channel", Text, nullable=False, server_default="api"),
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
document_fields = Table(
    "document_fields",
    metadata,
    Column("document_id", Uuid, ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True),
    Column("field_id", Uuid, ForeignKey("field_definitions.id"), primary_key=True),
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

# --- rules --------------------------------------------------------------------------------------

# A rule and the number of its current version; the definitions are in `rule_versions`.
rules = Table(
    "rules",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("scope", Text, nullable=False),
    Column("owner_id", Uuid, ForeignKey("users.id"), nullable=True, index=True),
    Column("current_version", Integer, nullable=False),
    Column("enabled", Boolean, nullable=False),
    Column("disabled_reason", Text, nullable=True),
    Column("deleted_at", UtcDateTime, nullable=True),
    Column("created_at", UtcDateTime, nullable=False),
    Column("updated_at", UtcDateTime, nullable=False),
    Column("version", Integer, nullable=False),
)

# Every version of a rule; never changed once written. `created_by` has no foreign key: the
# author may be deleted, the version stays.
rule_versions = Table(
    "rule_versions",
    metadata,
    Column("rule_id", Uuid, ForeignKey("rules.id", ondelete="CASCADE"), primary_key=True),
    Column("number", Integer, primary_key=True),
    Column("name", Text, nullable=False),
    Column("priority", Integer, nullable=False),
    Column("triggers", json_type(), nullable=False),
    Column("conditions", json_type(), nullable=False),
    Column("actions", json_type(), nullable=False),
    Column("created_at", UtcDateTime, nullable=False),
    Column("created_by", Uuid, nullable=True),
)

rule_applications = Table(
    "rule_applications",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("rule_id", Uuid, ForeignKey("rules.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("rule_version", Integer, nullable=False),
    Column("user_id", Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("status", Text, nullable=False),
    Column("documents", json_type(), nullable=False),
    Column("accept_conflicts", json_type(), nullable=False),
    Column("position", Integer, nullable=False),
    Column("applied", Integer, nullable=False),
    Column("unchanged", Integer, nullable=False),
    Column("skipped", json_type(), nullable=False),
    Column("error", Text, nullable=True),
    Column("created_at", UtcDateTime, nullable=False),
    Column("finished_at", UtcDateTime, nullable=True),
    Column("version", Integer, nullable=False),
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
    # Claims given back unfinished; they do not count against the retry policy.
    Column("releases", Integer, nullable=False, server_default="0"),
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
# in `event_retries` until `retry_at`.
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
    # When the delivery is due again; NULL: at once.
    Column("retry_at", UtcDateTime, nullable=True),
)

# --- identity: rows go with their user (ON DELETE CASCADE) ---------------------------------------

credentials = Table(
    "credentials",
    metadata,
    Column("user_id", Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("password_hash", Text, nullable=True),
    Column("password_changed_at", UtcDateTime, nullable=True),
    # TOTP: the secret encrypted (SecretCipher); NULL if TOTP is off.
    Column("totp_secret", LargeBinary, nullable=True),
    Column("totp_confirmed", Boolean, nullable=False),
    Column("totp_last_step", BigInteger, nullable=True),
    Column("version", Integer, nullable=False),
)

recovery_codes = Table(
    "recovery_codes",
    metadata,
    Column(
        "user_id",
        Uuid,
        ForeignKey("credentials.user_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("code_hash", String(64), primary_key=True),
)

sessions = Table(
    "sessions",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("user_id", Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("token_hash", String(64), nullable=False, unique=True),
    Column("method", Text, nullable=False),
    Column("created_at", UtcDateTime, nullable=False),
    Column("last_seen_at", UtcDateTime, nullable=False),
    Column("expires_at", UtcDateTime, nullable=False),
    Column("version", Integer, nullable=False),
)

api_tokens = Table(
    "api_tokens",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("user_id", Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("name", Text, nullable=False),
    Column("scope", Text, nullable=False),
    Column("token_hash", String(64), nullable=False, unique=True),
    Column("created_at", UtcDateTime, nullable=False),
    Column("expires_at", UtcDateTime, nullable=True),
    Column("last_used_at", UtcDateTime, nullable=True),
    Column("version", Integer, nullable=False),
)

external_identities = Table(
    "external_identities",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("issuer", Text, nullable=False),
    Column("subject", Text, nullable=False),
    Column("user_id", Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("created_at", UtcDateTime, nullable=False),
    Column("version", Integer, nullable=False),
    Index(None, "issuer", "subject", unique=True),
)

# Failed sign-ins per key (`account:<name>`, `source:<address>`).
login_failures = Table(
    "login_failures",
    metadata,
    Column("key", Text, primary_key=True),
    Column("failures", Integer, nullable=False),
    Column("first_failure_at", UtcDateTime, nullable=False),
    Column("blocked_until", UtcDateTime, nullable=True),
    Column("version", Integer, nullable=False),
)

# --- webhooks: rows go with their owner (ON DELETE CASCADE) ---------------------------------------

webhooks = Table(
    "webhooks",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column(
        "owner_id", Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    ),
    Column("name", Text, nullable=False),
    Column("url", Text, nullable=False),
    Column("event_types", json_type(), nullable=False),
    # The signing secrets as `SecretCipher` made them; the previous one only during the grace
    # period after renewing.
    Column("secret", LargeBinary, nullable=False),
    Column("previous_secret", LargeBinary, nullable=True),
    Column("previous_valid_until", UtcDateTime, nullable=True),
    Column("active", Boolean, nullable=False),
    Column("disabled_reason", Text, nullable=True),
    Column("failed_streak", Integer, nullable=False),
    Column("created_at", UtcDateTime, nullable=False),
    Column("updated_at", UtcDateTime, nullable=False),
    Column("version", Integer, nullable=False),
)

# The log of delivery attempts. The answer of the receiver is not kept. `document_id` has no
# foreign key: the document may be deleted while its log lives on.
webhook_deliveries = Table(
    "webhook_deliveries",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("webhook_id", Uuid, ForeignKey("webhooks.id", ondelete="CASCADE"), nullable=False),
    Column("event_id", Uuid, nullable=False),
    Column("event_type", Text, nullable=False),
    Column("document_id", Uuid, nullable=True),
    Column("attempt", Integer, nullable=False),
    Column("started_at", UtcDateTime, nullable=False),
    Column("duration_ms", Integer, nullable=False),
    Column("outcome", Text, nullable=False),
    Column("status_code", Integer, nullable=True),
    Column("error", Text, nullable=True),
    Column("next_attempt_at", UtcDateTime, nullable=True),
    Index(None, "webhook_id", "id"),
    Index(None, "started_at"),
)
