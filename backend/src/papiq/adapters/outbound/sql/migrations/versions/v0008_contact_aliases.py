"""Aliases of contacts and descriptions of document types

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-10 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("document_types", sa.Column("description", sa.Text(), nullable=True))
    op.create_table(
        "contact_aliases",
        sa.Column("contact_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("name_key", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["contact_id"],
            ["contacts.id"],
            name=op.f("fk_contact_aliases_contact_id_contacts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("name_key", name=op.f("pk_contact_aliases")),
    )
    op.create_index(
        op.f("ix_contact_aliases_contact_id"), "contact_aliases", ["contact_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_contact_aliases_contact_id"), table_name="contact_aliases")
    op.drop_table("contact_aliases")
    # Not a batch operation: on SQLite, it would copy the table, which documents refer to.
    op.drop_column("document_types", "description")
