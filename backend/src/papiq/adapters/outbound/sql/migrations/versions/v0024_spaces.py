"""Drawers become spaces

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-16 22:00:00.000000

The unit of filing and of permissions is called a space (Tobi, 16.10.2026: "Space" in German
and English). Tables, columns, keys and indexes take the name, and so do the names Papiq stores:
the rule action `set_space`, the variable `space` in templates, `space_id` in the outbox and in
the processing log, the field `space` in the log's checks and changes, the model version
`person:space`. Text a person wrote stays, as do the messages of older log entries.

Postgres renames in place. On SQLite a rename would keep the old names of keys and quote the new
names of tables, so the two tables are created under their new names and filled (as 0007 did),
and `documents` is built anew without a rename: `migrate` turns foreign keys off meanwhile, so
the rows that refer to a document stay, and checks them before the commit.

A template that already uses `space` for something else stops the revision (the rename would
change what it means).
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from liquid import parse
from liquid.exceptions import LiquidError

from papiq.adapters.outbound.sql import types

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LISTS_OF_FIELDS = frozenset({"changed", "accepted", "entered", "kept"})


def upgrade() -> None:
    _rename_tables("drawer", "space")
    _rename_stored("drawer", "space")


def downgrade() -> None:
    _rename_stored("space", "drawer")
    _rename_tables("space", "drawer")


# --- tables -------------------------------------------------------------------------------------


def _rename_tables(old: str, new: str) -> None:
    if op.get_bind().dialect.name == "sqlite":
        _rebuild(old, new)
    else:
        _rename_in_place(old, new)


def _constraints(name: str) -> list[tuple[str, str]]:
    return [
        (f"{name}s", f"pk_{name}s"),
        (f"{name}s", f"fk_{name}s_owner_id_users"),
        (f"{name}_shares", f"pk_{name}_shares"),
        (f"{name}_shares", f"fk_{name}_shares_{name}_id_{name}s"),
        (f"{name}_shares", f"fk_{name}_shares_user_id_users"),
        ("documents", f"fk_documents_{name}_id_{name}s"),
    ]


def _indexes(name: str) -> list[str]:
    return [
        f"ix_{name}s_owner_id_name_key",
        f"uq_{name}s_default_owner_id",
        f"ix_{name}_shares_user_id",
        f"ix_documents_{name}_id",
    ]


def _rename_in_place(old: str, new: str) -> None:
    op.rename_table(f"{old}s", f"{new}s")
    op.rename_table(f"{old}_shares", f"{new}_shares")
    op.alter_column(f"{new}_shares", f"{old}_id", new_column_name=f"{new}_id")
    op.alter_column("documents", f"{old}_id", new_column_name=f"{new}_id")
    for (_, before), (table, after) in zip(_constraints(old), _constraints(new), strict=True):
        op.execute(f"ALTER TABLE {table} RENAME CONSTRAINT {before} TO {after}")
    for before, after in zip(_indexes(old), _indexes(new), strict=True):
        op.execute(f"ALTER INDEX {before} RENAME TO {after}")


def _rebuild(old: str, new: str) -> None:
    connection = op.get_bind()
    if connection.exec_driver_sql("PRAGMA foreign_keys").scalar():
        raise RuntimeError(
            "revision 0024 builds tables anew on SQLite: run it with foreign keys off "
            "(as migrate does), or the rows that refer to them go too"
        )
    _create_spaces(new)
    op.execute(
        f"INSERT INTO {new}s (id, owner_id, name, name_key, is_default, created_at, version) "
        f"SELECT id, owner_id, name, name_key, is_default, created_at, version FROM {old}s"
    )
    op.execute(
        f"INSERT INTO {new}_shares ({new}_id, user_id, level) "
        f"SELECT {old}_id, user_id, level FROM {old}_shares"
    )
    _rebuild_documents(old, new)
    op.drop_table(f"{old}_shares")
    op.drop_table(f"{old}s")


def _create_spaces(name: str) -> None:
    op.create_table(
        f"{name}s",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("name_key", sa.Text(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("created_at", types.UtcDateTime(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"], name=op.f(f"fk_{name}s_owner_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{name}s")),
    )
    op.create_index(
        op.f(f"ix_{name}s_owner_id_name_key"), f"{name}s", ["owner_id", "name_key"], unique=True
    )
    op.create_index(
        f"uq_{name}s_default_owner_id",
        f"{name}s",
        ["owner_id"],
        unique=True,
        sqlite_where=sa.text("is_default"),
        postgresql_where=sa.text("is_default"),
    )
    op.create_table(
        f"{name}_shares",
        sa.Column(f"{name}_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("level", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            [f"{name}_id"],
            [f"{name}s.id"],
            name=op.f(f"fk_{name}_shares_{name}_id_{name}s"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f(f"fk_{name}_shares_user_id_users")
        ),
        sa.PrimaryKeyConstraint(f"{name}_id", "user_id", name=op.f(f"pk_{name}_shares")),
    )
    op.create_index(op.f(f"ix_{name}_shares_user_id"), f"{name}_shares", ["user_id"])


# The columns of `documents` besides the space, as `tables.py` has them since 0023.
_DOCUMENT_COLUMNS = (
    "id",
    "owner_id",
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


def _rebuild_documents(old: str, new: str) -> None:
    """`documents` with the space column under its new name, without renaming the table: a
    rename would quote the name and change the references of the tables that point to it."""
    connection = op.get_bind()
    count = connection.exec_driver_sql("SELECT count(*) FROM documents").scalar()
    op.execute("CREATE TEMP TABLE _documents_0024 AS SELECT * FROM documents")
    op.drop_table("documents")
    _create_documents(new)
    columns = ", ".join(_DOCUMENT_COLUMNS)
    op.execute(
        f"INSERT INTO documents ({columns}, {new}_id) "
        f"SELECT {columns}, {old}_id FROM _documents_0024"
    )
    op.execute("DROP TABLE _documents_0024")
    copied = connection.exec_driver_sql("SELECT count(*) FROM documents").scalar()
    if copied != count:
        raise RuntimeError(f"revision 0024 copied {copied} of {count} documents")


def _create_documents(space: str) -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column(f"{space}_id", sa.Uuid(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("title_key", sa.Text(), nullable=False, server_default=""),
        sa.Column("original_filename", sa.Text(), nullable=False),
        sa.Column("media_type", sa.Text(), nullable=False),
        sa.Column("contact_id", sa.Uuid(), nullable=True),
        sa.Column("document_type_id", sa.Uuid(), nullable=True),
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
            [f"{space}_id"], [f"{space}s.id"], name=op.f(f"fk_documents_{space}_id_{space}s")
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
    )
    op.create_index(op.f(f"ix_documents_{space}_id"), "documents", [f"{space}_id"])
    op.create_index(op.f("ix_documents_confirmed_at"), "documents", ["confirmed_at"])
    op.create_index(
        op.f("ix_documents_owner_id_sha256"), "documents", ["owner_id", "sha256"], unique=True
    )
    op.create_index(op.f("ix_documents_document_date_id"), "documents", ["document_date", "id"])
    op.create_index(op.f("ix_documents_title_key_id"), "documents", ["title_key", "id"])


# --- stored names -------------------------------------------------------------------------------


def _rename_stored(old: str, new: str) -> None:
    _rename_in_rules(old, new)
    _rename_in_title_patterns(old, new)
    _rename_in_outbox(old, new)
    _rename_in_log(old, new)


def _rename_in_rules(old: str, new: str) -> None:
    """The action that sets the space, and the variable in templates (titles, values of text
    fields, reasons of reviews)."""
    connection = op.get_bind()
    fields = sa.table("field_definitions", sa.column("id", sa.Uuid()), sa.column("data_type"))
    texts = {
        str(row.id)
        for row in connection.execute(sa.select(fields).where(fields.c.data_type == "text"))
    }
    versions = sa.table(
        "rule_versions",
        sa.column("rule_id", sa.Uuid()),
        sa.column("number", sa.Integer()),
        sa.column("actions", types.json_type()),
    )
    for row in connection.execute(sa.select(versions)).mappings().all():
        actions = [_action(action, texts, old, new) for action in row["actions"]]
        if actions != row["actions"]:
            connection.execute(
                versions.update()
                .where(versions.c.rule_id == row["rule_id"], versions.c.number == row["number"])
                .values(actions=actions)
            )


def _action(action: dict[str, Any], texts: set[str], old: str, new: str) -> dict[str, Any]:
    match action.get("type"):
        case kind if kind == f"set_{old}":
            return _renamed_keys(action, {"type": f"set_{new}"}, {f"{old}_id": f"{new}_id"})
        case "set_title":
            return {**action, "template": _template(action["template"], old, new)}
        case "set_field" if action.get("field_id") in texts and isinstance(action["value"], str):
            return {**action, "value": _template(action["value"], old, new)}
        case "force_review":
            return {**action, "reason": _template(action["reason"], old, new)}
    return action


def _rename_in_title_patterns(old: str, new: str) -> None:
    connection = op.get_bind()
    types_ = sa.table(
        "document_types", sa.column("id", sa.Uuid()), sa.column("title_pattern", sa.Text())
    )
    query = sa.select(types_).where(types_.c.title_pattern.is_not(None))
    for row in connection.execute(query).all():
        pattern = _template(row.title_pattern, old, new)
        if pattern != row.title_pattern:
            connection.execute(
                types_.update().where(types_.c.id == row.id).values(title_pattern=pattern)
            )


def _template(template: str, old: str, new: str) -> str:
    """`template` with the global variable `old` named `new`, by the places Liquid's analysis
    gives; text, strings and raw blocks stay. RuntimeError if `new` means something else in it
    already."""
    try:
        places = sorted(variable.span.index for variable in _globals(template).get(old, []))
    except LiquidError:
        return template  # never saved like this (templates are checked); leave it as it is
    if not places:
        return template
    pieces, start, expected = [], 0, []
    for number, place in enumerate(places):
        if template.startswith(old, place):
            at = place
        elif template.startswith((f"['{old}']", f'["{old}"]'), place):
            at = place + 2
        else:
            raise RuntimeError(f"revision 0024 cannot read '{old}' in the template {template!r}")
        pieces += [template[start:at], new]
        start = at + len(old)
        expected.append(place + number * (len(new) - len(old)))
    renamed = "".join([*pieces, template[start:]])
    found = sorted(variable.span.index for variable in _globals(renamed).get(new, []))
    if found != expected or _globals(renamed).get(old):
        raise RuntimeError(
            f"the template {template!r} uses '{new}' already; rename it there before upgrading"
        )
    return renamed


def _globals(template: str) -> dict[str, Any]:
    return dict(parse(template).analyze().globals)


def _rename_in_outbox(old: str, new: str) -> None:
    connection = op.get_bind()
    outbox = sa.table(
        "outbox",
        sa.column("seq", sa.Integer()),
        sa.column("type", sa.Text()),
        sa.column("payload", types.json_type()),
    )
    query = sa.select(outbox).where(outbox.c.type == "document.filed")
    for row in connection.execute(query).mappings().all():
        payload = _renamed_keys(row["payload"], {}, {f"{old}_id": f"{new}_id"})
        if payload != row["payload"]:
            connection.execute(
                outbox.update().where(outbox.c.seq == row["seq"]).values(payload=payload)
            )


def _rename_in_log(old: str, new: str) -> None:
    """The person's choice of the space (`person:<name>`, its `<name>_id`), the field in checks
    and changes, and the key of the chosen space."""
    connection = op.get_bind()
    log = sa.table(
        "processing_log",
        sa.column("seq", sa.Integer()),
        sa.column("model_version", sa.Text()),
        sa.column("input", types.json_type()),
        sa.column("output", types.json_type()),
    )
    connection.execute(
        log.update()
        .where(log.c.model_version == f"person:{old}")
        .values(model_version=f"person:{new}")
    )
    mentions = sa.or_(
        sa.cast(log.c.input, sa.Text).like(f"%{old}%"),
        sa.cast(log.c.output, sa.Text).like(f"%{old}%"),
    )
    rename = _Renamer(old, new)
    for row in connection.execute(sa.select(log).where(mentions)).mappings().all():
        values = {column: rename.walk(row[column]) for column in ("input", "output")}
        if any(values[column] != row[column] for column in values):
            connection.execute(log.update().where(log.c.seq == row["seq"]).values(**values))


class _Renamer:
    """Renames the key `<old>_id` wherever it is, and the field `<old>` where a field is named:
    the value of `field`, an item of `changed`, `accepted`, `entered` or `kept`."""

    def __init__(self, old: str, new: str) -> None:
        self.old, self.new = old, new

    def walk(self, value: Any, key: str | None = None) -> Any:
        if isinstance(value, dict):
            keys = {f"{self.old}_id": f"{self.new}_id"}
            return {
                keys.get(name, name): (
                    self.new if name == "field" and item == self.old else self.walk(item, name)
                )
                for name, item in value.items()
            }
        if isinstance(value, list):
            if key in _LISTS_OF_FIELDS:
                return [self.new if item == self.old else self.walk(item) for item in value]
            return [self.walk(item) for item in value]
        return value


def _renamed_keys(
    value: dict[str, Any], values: dict[str, Any], keys: dict[str, str]
) -> dict[str, Any]:
    """`value` with `keys` renamed in place and `values` set."""
    return {keys.get(name, name): values.get(name, item) for name, item in value.items()}
