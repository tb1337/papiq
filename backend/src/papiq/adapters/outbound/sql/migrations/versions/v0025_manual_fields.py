"""Fields set by hand

Revision ID: 0025
Revises: 0024
Create Date: 2026-10-16 23:00:00.000000

A field is read by the language model (`auto`) or set by hand only (`manual`, issue #52).
Every field so far is read.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Not a batch operation: on SQLite, it would copy the table, which others refer to.
    op.add_column(
        "field_definitions",
        sa.Column("assignment", sa.Text(), nullable=False, server_default="auto"),
    )


def downgrade() -> None:
    op.drop_column("field_definitions", "assignment")
