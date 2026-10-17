"""Icons, colours and logos of master data

Revision ID: 0028
Revises: 0027
Create Date: 2026-10-17 12:00:00.000000

Tags and document types get an icon (the name of a Lucide icon), tags a colour (one of the
palette or `#rrggbb`), contacts a logo: where it came from, the domain of a fetched one and when
it was stored, and the domains whose fetch found none. Nothing has any of them yet.

The columns are nullable and without keys, so both databases add them in place (not as a batch
operation: on SQLite that would copy the tables, which others refer to). The downgrade drops
them; the logo files stay in the object store, unreferenced.
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

from papiq.adapters.outbound.sql import types

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS: tuple[tuple[str, str, sa.types.TypeEngine[Any]], ...] = (
    ("tags", "icon", sa.Text()),
    ("tags", "color", sa.Text()),
    ("document_types", "icon", sa.Text()),
    ("contacts", "logo_source", sa.Text()),
    ("contacts", "logo_domain", sa.Text()),
    ("contacts", "logo_stored_at", types.UtcDateTime()),
    ("contacts", "logo_failed_domains", sa.Text()),
    ("contacts", "logo_failed_at", types.UtcDateTime()),
)


def upgrade() -> None:
    for table, column, kind in _COLUMNS:
        op.add_column(table, sa.Column(column, kind, nullable=True))


def downgrade() -> None:
    for table, column, _ in reversed(_COLUMNS):
        op.drop_column(table, column)
