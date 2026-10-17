"""Tags with scopes, nesting and groups

Revision ID: 0026
Revises: 0025
Create Date: 2026-10-17 01:00:00.000000

A tag has a scope (`document`, `contact`: a contact group, `document_type`: a type group), a
parent of the same scope, an assignment (`auto`, `manual`) and whether documents show it. Names
are unique per scope instead of across all tags. Contacts and document types are in groups
(`contact_groups`, `document_type_groups`). Every tag so far describes documents, is proposed by
the classification, shown, and at the top.

Postgres changes `tags` in place. On SQLite the unique key on the name stands in the table's
`CREATE TABLE`, so `tags` is built anew without a rename, as 0024 builds `documents`: a copy,
the table dropped and created, the rows copied back. `migrate` turns foreign keys off meanwhile,
so the rows that refer to a tag (`document_tags`) stay, and checks them before the commit.

The downgrade drops the groups with their tags and the nesting; rules that use groups are left
as they are (as 0020 leaves rules on periods).
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

from papiq.adapters.outbound.sql import types

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_COLUMNS = ("id", "name", "name_key", "created_at", "version")


def upgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        _rebuild(nested=True)
    else:
        op.add_column(
            "tags", sa.Column("scope", sa.Text(), nullable=False, server_default="document")
        )
        op.add_column("tags", sa.Column("parent_id", sa.Uuid(), nullable=True))
        op.add_column(
            "tags", sa.Column("assignment", sa.Text(), nullable=False, server_default="auto")
        )
        op.add_column(
            "tags", sa.Column("shown", sa.Boolean(), nullable=False, server_default=sa.true())
        )
        op.create_foreign_key(op.f("fk_tags_parent_id_tags"), "tags", "tags", ["parent_id"], ["id"])
        op.create_index(op.f("ix_tags_parent_id"), "tags", ["parent_id"])
        op.drop_constraint("uq_tags_name_key", "tags", type_="unique")
        op.create_unique_constraint(op.f("uq_tags_scope_name_key"), "tags", ["scope", "name_key"])
    _create_groups("contact_groups", "contact_id", "contacts")
    _create_groups("document_type_groups", "document_type_id", "document_types")


def downgrade() -> None:
    op.drop_table("document_type_groups")
    op.drop_table("contact_groups")
    tags = sa.table(
        "tags",
        sa.column("id", sa.Uuid()),
        sa.column("scope", sa.Text()),
        sa.column("parent_id", sa.Uuid()),
    )
    op.execute(tags.update().values(parent_id=None))
    op.execute(tags.delete().where(tags.c.scope != "document"))
    if op.get_bind().dialect.name == "sqlite":
        _rebuild(nested=False)
    else:
        op.drop_constraint("uq_tags_scope_name_key", "tags", type_="unique")
        op.create_unique_constraint(op.f("uq_tags_name_key"), "tags", ["name_key"])
        op.drop_index(op.f("ix_tags_parent_id"), "tags")
        op.drop_constraint("fk_tags_parent_id_tags", "tags", type_="foreignkey")
        for column in ("shown", "assignment", "parent_id", "scope"):
            op.drop_column("tags", column)


def _create_groups(name: str, owner: str, owners: str) -> None:
    op.create_table(
        name,
        sa.Column(owner, sa.Uuid(), nullable=False),
        sa.Column("tag_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            [owner], [f"{owners}.id"], name=op.f(f"fk_{name}_{owner}_{owners}"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["tag_id"], ["tags.id"], name=op.f(f"fk_{name}_tag_id_tags")),
        sa.PrimaryKeyConstraint(owner, "tag_id", name=op.f(f"pk_{name}")),
    )
    op.create_index(op.f(f"ix_{name}_tag_id"), name, ["tag_id"])


def _rebuild(*, nested: bool) -> None:
    """`tags` as it is with (`nested`) or before this revision, without renaming the table: a
    rename would quote the name and change the references of the tables that point to it."""
    connection = op.get_bind()
    if connection.exec_driver_sql("PRAGMA foreign_keys").scalar():
        raise RuntimeError(
            "revision 0026 builds tags anew on SQLite: run it with foreign keys off "
            "(as migrate does), or the rows that refer to them go too"
        )
    count = connection.exec_driver_sql("SELECT count(*) FROM tags").scalar()
    op.execute("CREATE TEMP TABLE _tags_0026 AS SELECT * FROM tags")
    op.drop_table("tags")
    _create_tags(nested=nested)
    columns = ", ".join(_OLD_COLUMNS)
    op.execute(f"INSERT INTO tags ({columns}) SELECT {columns} FROM _tags_0026")
    op.execute("DROP TABLE _tags_0026")
    copied = connection.exec_driver_sql("SELECT count(*) FROM tags").scalar()
    if copied != count:
        raise RuntimeError(f"revision 0026 copied {copied} of {count} tags")


def _create_tags(*, nested: bool) -> None:
    columns: list[sa.Column[Any] | sa.Constraint] = [
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("name_key", sa.Text(), nullable=False),
    ]
    if nested:
        columns += [
            sa.Column("scope", sa.Text(), nullable=False, server_default="document"),
            sa.Column("parent_id", sa.Uuid(), nullable=True),
            sa.Column("assignment", sa.Text(), nullable=False, server_default="auto"),
            sa.Column("shown", sa.Boolean(), nullable=False, server_default=sa.true()),
        ]
    columns += [
        sa.Column("created_at", types.UtcDateTime(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tags")),
    ]
    if nested:
        columns += [
            sa.ForeignKeyConstraint(
                ["parent_id"], ["tags.id"], name=op.f("fk_tags_parent_id_tags")
            ),
            sa.UniqueConstraint("scope", "name_key", name=op.f("uq_tags_scope_name_key")),
        ]
    else:
        columns.append(sa.UniqueConstraint("name_key", name=op.f("uq_tags_name_key")))
    op.create_table("tags", *columns)
    if nested:
        op.create_index(op.f("ix_tags_parent_id"), "tags", ["parent_id"])
