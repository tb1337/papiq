"""Topics at contacts, identifiers and documents

Revision ID: 0027
Revises: 0026
Create Date: 2026-10-17 09:00:00.000000

A topic is a tag of scope `topic`. A contact enables topics (`contact_topics`), an identifier of
a contact may point to one of them (`contact_identifiers.topic_id`), and a document carries one
at most (`documents.topic_id`, with an index for the filter). Nothing has a topic yet.

Postgres adds the columns in place. On SQLite a column with a foreign key added in place would
not carry the key's name in the table's `CREATE TABLE`, so `documents` and
`contact_identifiers` are built anew without a rename, as 0024 builds `documents`: a copy, the
table dropped and created, the rows copied back. `migrate` turns foreign keys off meanwhile, so
the rows that refer to a document stay, and checks them before the commit.

The downgrade drops the columns and `contact_topics`, and deletes the topics; rules that use
topics are left as they are (as 0026 leaves rules on groups).
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

from papiq.adapters.outbound.sql import types

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The columns of `documents` and `contact_identifiers` as `tables.py` has them since 0024,
# besides the topic.
_DOCUMENT_COLUMNS = (
    "id",
    "owner_id",
    "space_id",
    "sha256",
    "title",
    "title_key",
    "original_filename",
    "media_type",
    "contact_id",
    "document_type_id",
    "document_date",
    "channel",
    "lane",
    "processing_status",
    "processing_run",
    "processing_nodes",
    "processing_vision",
    "page_count",
    "confirmed_at",
    "created_at",
    "updated_at",
    "version",
)
_IDENTIFIER_COLUMNS = ("contact_id", "kind", "value", "source")


def upgrade() -> None:
    op.create_table(
        "contact_topics",
        sa.Column("contact_id", sa.Uuid(), nullable=False),
        sa.Column("tag_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["contact_id"],
            ["contacts.id"],
            name=op.f("fk_contact_topics_contact_id_contacts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tag_id"], ["tags.id"], name=op.f("fk_contact_topics_tag_id_tags")
        ),
        sa.PrimaryKeyConstraint("contact_id", "tag_id", name=op.f("pk_contact_topics")),
    )
    op.create_index(op.f("ix_contact_topics_tag_id"), "contact_topics", ["tag_id"])
    if op.get_bind().dialect.name == "sqlite":
        _rebuild(topics=True)
        return
    op.add_column("documents", sa.Column("topic_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_documents_topic_id_tags"), "documents", "tags", ["topic_id"], ["id"]
    )
    op.create_index(op.f("ix_documents_topic_id"), "documents", ["topic_id"])
    op.add_column("contact_identifiers", sa.Column("topic_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_contact_identifiers_topic_id_tags"),
        "contact_identifiers",
        "tags",
        ["topic_id"],
        ["id"],
    )


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        _rebuild(topics=False)
    else:
        op.drop_constraint("fk_contact_identifiers_topic_id_tags", "contact_identifiers")
        op.drop_column("contact_identifiers", "topic_id")
        op.drop_index(op.f("ix_documents_topic_id"), "documents")
        op.drop_constraint("fk_documents_topic_id_tags", "documents")
        op.drop_column("documents", "topic_id")
    op.drop_table("contact_topics")
    tags = sa.table(
        "tags",
        sa.column("id", sa.Uuid()),
        sa.column("scope", sa.Text()),
        sa.column("parent_id", sa.Uuid()),
    )
    topics = tags.c.scope == "topic"
    op.execute(tags.update().where(topics).values(parent_id=None))
    op.execute(tags.delete().where(topics))


def _rebuild(*, topics: bool) -> None:
    """`documents` and `contact_identifiers` with (`topics`) or without their topic, without
    renaming the tables: a rename would quote the names and change the references of the
    tables that point to them."""
    connection = op.get_bind()
    if connection.exec_driver_sql("PRAGMA foreign_keys").scalar():
        raise RuntimeError(
            "revision 0027 builds documents anew on SQLite: run it with foreign keys off "
            "(as migrate does), or the rows that refer to them go too"
        )
    for table, columns, create in (
        ("documents", _DOCUMENT_COLUMNS, _create_documents),
        ("contact_identifiers", _IDENTIFIER_COLUMNS, _create_identifiers),
    ):
        count = connection.exec_driver_sql(f"SELECT count(*) FROM {table}").scalar()
        op.execute(f"CREATE TEMP TABLE _{table}_0027 AS SELECT * FROM {table}")
        op.drop_table(table)
        create(topics=topics)
        listed = ", ".join(columns)
        op.execute(f"INSERT INTO {table} ({listed}) SELECT {listed} FROM _{table}_0027")
        op.execute(f"DROP TABLE _{table}_0027")
        copied = connection.exec_driver_sql(f"SELECT count(*) FROM {table}").scalar()
        if copied != count:
            raise RuntimeError(f"revision 0027 copied {copied} of {count} rows of {table}")


def _create_documents(*, topics: bool) -> None:
    columns: list[sa.Column[Any] | sa.Constraint] = [
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("space_id", sa.Uuid(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("title_key", sa.Text(), nullable=False, server_default=""),
        sa.Column("original_filename", sa.Text(), nullable=False),
        sa.Column("media_type", sa.Text(), nullable=False),
        sa.Column("contact_id", sa.Uuid(), nullable=True),
        sa.Column("document_type_id", sa.Uuid(), nullable=True),
    ]
    if topics:
        columns.append(sa.Column("topic_id", sa.Uuid(), nullable=True))
    columns += [
        sa.Column("document_date", sa.Date(), nullable=True),
        sa.Column("channel", sa.Text(), nullable=False, server_default="api"),
        sa.Column("lane", sa.Text(), nullable=True),
        sa.Column("processing_status", sa.Text(), nullable=False),
        sa.Column("processing_run", sa.Integer(), nullable=False),
        sa.Column("processing_nodes", types.json_type(), nullable=False, server_default="{}"),
        sa.Column("processing_vision", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("confirmed_at", types.UtcDateTime(), nullable=True),
        sa.Column("created_at", types.UtcDateTime(), nullable=False),
        sa.Column("updated_at", types.UtcDateTime(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"], name=op.f("fk_documents_owner_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["space_id"], ["spaces.id"], name=op.f("fk_documents_space_id_spaces")
        ),
        sa.ForeignKeyConstraint(
            ["contact_id"], ["contacts.id"], name=op.f("fk_documents_contact_id_contacts")
        ),
        sa.ForeignKeyConstraint(
            ["document_type_id"],
            ["document_types.id"],
            name=op.f("fk_documents_document_type_id_document_types"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_documents")),
    ]
    if topics:
        columns.append(
            sa.ForeignKeyConstraint(
                ["topic_id"], ["tags.id"], name=op.f("fk_documents_topic_id_tags")
            )
        )
    op.create_table("documents", *columns)
    op.create_index(op.f("ix_documents_space_id"), "documents", ["space_id"])
    op.create_index(op.f("ix_documents_confirmed_at"), "documents", ["confirmed_at"])
    op.create_index(
        op.f("ix_documents_owner_id_sha256"), "documents", ["owner_id", "sha256"], unique=True
    )
    op.create_index(op.f("ix_documents_document_date_id"), "documents", ["document_date", "id"])
    op.create_index(op.f("ix_documents_title_key_id"), "documents", ["title_key", "id"])
    if topics:
        op.create_index(op.f("ix_documents_topic_id"), "documents", ["topic_id"])


def _create_identifiers(*, topics: bool) -> None:
    columns: list[sa.Column[Any] | sa.Constraint] = [
        sa.Column("contact_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["contact_id"],
            ["contacts.id"],
            name=op.f("fk_contact_identifiers_contact_id_contacts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("kind", "value", name=op.f("pk_contact_identifiers")),
    ]
    if topics:
        columns += [
            sa.Column("topic_id", sa.Uuid(), nullable=True),
            sa.ForeignKeyConstraint(
                ["topic_id"], ["tags.id"], name=op.f("fk_contact_identifiers_topic_id_tags")
            ),
        ]
    op.create_table("contact_identifiers", *columns)
    op.create_index(
        op.f("ix_contact_identifiers_contact_id"), "contact_identifiers", ["contact_id"]
    )
