"""Rules: rules, their versions and applications; the intake channel of documents

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-07 22:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from papiq.adapters.outbound.sql import types

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "documents", sa.Column("channel", sa.Text(), nullable=False, server_default="api")
    )
    op.create_table(
        "rules",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=True),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("disabled_reason", sa.Text(), nullable=True),
        sa.Column("deleted_at", types.UtcDateTime(), nullable=True),
        sa.Column("created_at", types.UtcDateTime(), nullable=False),
        sa.Column("updated_at", types.UtcDateTime(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name=op.f("fk_rules_owner_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rules")),
    )
    op.create_index(op.f("ix_rules_owner_id"), "rules", ["owner_id"])
    op.create_table(
        "rule_versions",
        sa.Column("rule_id", sa.Uuid(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("triggers", types.json_type(), nullable=False),
        sa.Column("conditions", types.json_type(), nullable=False),
        sa.Column("actions", types.json_type(), nullable=False),
        sa.Column("created_at", types.UtcDateTime(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["rule_id"],
            ["rules.id"],
            name=op.f("fk_rule_versions_rule_id_rules"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("rule_id", "number", name=op.f("pk_rule_versions")),
    )
    op.create_table(
        "rule_applications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("rule_id", sa.Uuid(), nullable=False),
        sa.Column("rule_version", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("documents", types.json_type(), nullable=False),
        sa.Column("accept_conflicts", types.json_type(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("applied", sa.Integer(), nullable=False),
        sa.Column("unchanged", sa.Integer(), nullable=False),
        sa.Column("skipped", types.json_type(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", types.UtcDateTime(), nullable=False),
        sa.Column("finished_at", types.UtcDateTime(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["rule_id"],
            ["rules.id"],
            name=op.f("fk_rule_applications_rule_id_rules"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_rule_applications_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rule_applications")),
    )
    op.create_index(op.f("ix_rule_applications_rule_id"), "rule_applications", ["rule_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_rule_applications_rule_id"), table_name="rule_applications")
    op.drop_table("rule_applications")
    op.drop_table("rule_versions")
    op.drop_index(op.f("ix_rules_owner_id"), table_name="rules")
    op.drop_table("rules")
    with op.batch_alter_table("documents") as batch:
        batch.drop_column("channel")
