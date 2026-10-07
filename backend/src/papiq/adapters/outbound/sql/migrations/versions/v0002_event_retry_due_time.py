"""Event retries: when a failed delivery is due again

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-07 06:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from papiq.adapters.outbound.sql import types

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # NULL: due at once (retries recorded before this revision).
    op.add_column("event_retries", sa.Column("retry_at", types.UtcDateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("event_retries") as batch:
        batch.drop_column("retry_at")
