"""Initial schema

Revision ID: 0001
Revises:
Create Date: 2026-10-06 19:10:46.487637
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from papiq.adapters.outbound.sql import types

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "attribute_definitions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("name_key", sa.Text(), nullable=False),
        sa.Column("data_type", sa.Text(), nullable=False),
        sa.Column("is_global", sa.Boolean(), nullable=False),
        sa.Column(
            "choices",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("created_at", types.UtcDateTime(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_attribute_definitions")),
        sa.UniqueConstraint("name_key", name=op.f("uq_attribute_definitions_name_key")),
    )
    op.create_table(
        "contacts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("name_key", sa.Text(), nullable=False),
        sa.Column("created_at", types.UtcDateTime(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_contacts")),
        sa.UniqueConstraint("name_key", name=op.f("uq_contacts_name_key")),
    )
    op.create_table(
        "document_types",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("name_key", sa.Text(), nullable=False),
        sa.Column("created_at", types.UtcDateTime(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_types")),
        sa.UniqueConstraint("name_key", name=op.f("uq_document_types_name_key")),
    )
    op.create_table(
        "event_subscriptions",
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("position", sa.BigInteger(), nullable=False),
        sa.Column("created_at", types.UtcDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("name", name=op.f("pk_event_subscriptions")),
    )
    op.create_table(
        "jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column(
            "payload",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("dedup_key", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("run_at", types.UtcDateTime(), nullable=False),
        sa.Column("locked_until", types.UtcDateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_jobs")),
    )
    op.create_index(op.f("ix_jobs_status_run_at"), "jobs", ["status", "run_at"], unique=False)
    op.create_index(
        "uq_jobs_active_dedup_key",
        "jobs",
        ["dedup_key"],
        unique=True,
        sqlite_where=sa.text("status IN ('queued', 'running')"),
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )

    op.create_table(
        "outbox",
        sa.Column(
            "seq",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("occurred_at", types.UtcDateTime(), nullable=False),
        sa.Column(
            "payload",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("recorded_at", types.UtcDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("seq", name=op.f("pk_outbox")),
        sa.UniqueConstraint("event_id", name=op.f("uq_outbox_event_id")),
        sqlite_autoincrement=True,
    )
    op.create_table(
        "tags",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("name_key", sa.Text(), nullable=False),
        sa.Column("created_at", types.UtcDateTime(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tags")),
        sa.UniqueConstraint("name_key", name=op.f("uq_tags_name_key")),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("username_key", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("created_at", types.UtcDateTime(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("username_key", name=op.f("uq_users_username_key")),
    )
    op.create_table(
        "attribute_document_types",
        sa.Column("attribute_id", sa.Uuid(), nullable=False),
        sa.Column("document_type_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["attribute_id"],
            ["attribute_definitions.id"],
            name=op.f("fk_attribute_document_types_attribute_id_attribute_definitions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_type_id"],
            ["document_types.id"],
            name=op.f("fk_attribute_document_types_document_type_id_document_types"),
        ),
        sa.PrimaryKeyConstraint(
            "attribute_id", "document_type_id", name=op.f("pk_attribute_document_types")
        ),
    )
    op.create_table(
        "drawers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("name_key", sa.Text(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("created_at", types.UtcDateTime(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name=op.f("fk_drawers_owner_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_drawers")),
    )
    op.create_index(
        op.f("ix_drawers_owner_id_name_key"), "drawers", ["owner_id", "name_key"], unique=True
    )
    op.create_index(
        "uq_drawers_default_owner_id",
        "drawers",
        ["owner_id"],
        unique=True,
        sqlite_where=sa.text("is_default"),
        postgresql_where=sa.text("is_default"),
    )

    op.create_table(
        "event_retries",
        sa.Column("subscriber", sa.Text(), nullable=False),
        sa.Column("seq", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["seq"], ["outbox.seq"], name=op.f("fk_event_retries_seq_outbox"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["subscriber"],
            ["event_subscriptions.name"],
            name=op.f("fk_event_retries_subscriber_event_subscriptions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("subscriber", "seq", name=op.f("pk_event_retries")),
    )
    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("drawer_id", sa.Uuid(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("original_filename", sa.Text(), nullable=False),
        sa.Column("media_type", sa.Text(), nullable=False),
        sa.Column("contact_id", sa.Uuid(), nullable=True),
        sa.Column("document_type_id", sa.Uuid(), nullable=True),
        sa.Column("document_date", sa.Date(), nullable=True),
        sa.Column("lane", sa.Text(), nullable=True),
        sa.Column("processing_status", sa.Text(), nullable=False),
        sa.Column("processing_step", sa.Text(), nullable=True),
        sa.Column("processing_run", sa.Integer(), nullable=False),
        sa.Column(
            "processing_outcomes",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("created_at", types.UtcDateTime(), nullable=False),
        sa.Column("updated_at", types.UtcDateTime(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["contact_id"], ["contacts.id"], name=op.f("fk_documents_contact_id_contacts")
        ),
        sa.ForeignKeyConstraint(
            ["document_type_id"],
            ["document_types.id"],
            name=op.f("fk_documents_document_type_id_document_types"),
        ),
        sa.ForeignKeyConstraint(
            ["drawer_id"], ["drawers.id"], name=op.f("fk_documents_drawer_id_drawers")
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"], name=op.f("fk_documents_owner_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_documents")),
    )
    op.create_index(op.f("ix_documents_drawer_id"), "documents", ["drawer_id"], unique=False)
    op.create_index(
        op.f("ix_documents_owner_id_sha256"), "documents", ["owner_id", "sha256"], unique=True
    )

    op.create_table(
        "drawer_shares",
        sa.Column("drawer_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("level", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["drawer_id"],
            ["drawers.id"],
            name=op.f("fk_drawer_shares_drawer_id_drawers"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_drawer_shares_user_id_users")
        ),
        sa.PrimaryKeyConstraint("drawer_id", "user_id", name=op.f("pk_drawer_shares")),
    )
    op.create_index(op.f("ix_drawer_shares_user_id"), "drawer_shares", ["user_id"], unique=False)

    op.create_table(
        "document_attributes",
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("attribute_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("value_text", sa.Text(), nullable=True),
        sa.Column("value_decimal", types.ExactDecimal(), nullable=True),
        sa.Column("value_currency", sa.String(length=3), nullable=True),
        sa.Column("value_date", sa.Date(), nullable=True),
        sa.Column("value_boolean", sa.Boolean(), nullable=True),
        sa.ForeignKeyConstraint(
            ["attribute_id"],
            ["attribute_definitions.id"],
            name=op.f("fk_document_attributes_attribute_id_attribute_definitions"),
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_document_attributes_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("document_id", "attribute_id", name=op.f("pk_document_attributes")),
    )
    op.create_table(
        "document_tags",
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("tag_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_document_tags_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["tag_id"], ["tags.id"], name=op.f("fk_document_tags_tag_id_tags")),
        sa.PrimaryKeyConstraint("document_id", "tag_id", name=op.f("pk_document_tags")),
    )
    op.create_table(
        "processing_log",
        sa.Column(
            "seq",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("step", sa.Text(), nullable=False),
        sa.Column("run", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Double(), nullable=True),
        sa.Column("model_version", sa.Text(), nullable=True),
        sa.Column(
            "input",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "output",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("pipeline_version", sa.Text(), nullable=False),
        sa.Column("started_at", types.UtcDateTime(), nullable=False),
        sa.Column("duration_us", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_processing_log_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("seq", name=op.f("pk_processing_log")),
    )
    op.create_index(
        op.f("ix_processing_log_document_id"), "processing_log", ["document_id"], unique=False
    )


def downgrade() -> None:
    op.drop_table("processing_log")
    op.drop_table("document_tags")
    op.drop_table("document_attributes")
    op.drop_table("drawer_shares")
    op.drop_table("documents")
    op.drop_table("event_retries")
    op.drop_table("drawers")
    op.drop_table("attribute_document_types")
    op.drop_table("users")
    op.drop_table("tags")
    op.drop_table("outbox")
    op.drop_table("jobs")
    op.drop_table("event_subscriptions")
    op.drop_table("document_types")
    op.drop_table("contacts")
    op.drop_table("attribute_definitions")
