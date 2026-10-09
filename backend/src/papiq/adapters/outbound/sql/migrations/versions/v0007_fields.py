"""Attributes are called fields: tables, columns and the stored JSON

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-09 21:00:00.000000

The tables are created under the new names, filled and the old ones dropped, so constraint
names follow the naming convention on SQLite and Postgres alike. Stored names change too:
the step `extract_attributes`, the rule condition field `attribute` with `attribute_id`, the
action `set_attribute`, field checks `attribute:<id>` and the keys `attributes` in the
processing log, rules, jobs and events.

The downgrade restores the tables and the unambiguous names; the key and value `fields` stays,
because checks were stored under `fields` before as well.
"""

import re
from collections.abc import Callable, Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

from papiq.adapters.outbound.sql import types

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Renamed strings, as keys and as values; "attribute" (the rule condition field) as values only.
_UP = {
    "attributes": "fields",
    "attribute_id": "field_id",
    "extract_attributes": "extract_fields",
    "set_attribute": "set_field",
}
_UP_VALUES = {**_UP, "attribute": "field"}
# "fields" stays: checks were stored under `fields` before as well.
_DOWN = {new: old for old, new in _UP.items() if new != "fields"}
_DOWN_VALUES = {**_DOWN, "field": "attribute"}

# JSON columns that may hold the old names, by table, with the columns that identify a row.
_JSON_COLUMNS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "documents": (("id",), ("processing_outcomes",)),
    "processing_log": (("seq",), ("input", "output")),
    "rule_versions": (("rule_id", "number"), ("conditions", "actions")),
    "rule_applications": (("id",), ("documents", "accept_conflicts", "skipped")),
    "jobs": (("id",), ("payload",)),
    "outbox": (("seq",), ("payload",)),
}
# Field checks are named `attribute:<id>`.
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_STEP_COLUMNS = (("documents", "processing_step"), ("processing_log", "step"))


def upgrade() -> None:
    _create("field", "field_definitions", "field_document_types", "document_fields")
    _copy(
        ("attribute_definitions", "attribute_document_types", "document_attributes"),
        ("field_definitions", "field_document_types", "document_fields"),
        old_id="attribute_id",
        new_id="field_id",
    )
    op.drop_table("document_attributes")
    op.drop_table("attribute_document_types")
    op.drop_table("attribute_definitions")
    _rename_stored(_UP, _UP_VALUES, "attribute:", "field:")


def downgrade() -> None:
    _create("attribute", "attribute_definitions", "attribute_document_types", "document_attributes")
    _copy(
        ("field_definitions", "field_document_types", "document_fields"),
        ("attribute_definitions", "attribute_document_types", "document_attributes"),
        old_id="field_id",
        new_id="attribute_id",
    )
    op.drop_table("document_fields")
    op.drop_table("field_document_types")
    op.drop_table("field_definitions")
    _rename_stored(_DOWN, _DOWN_VALUES, "field:", "attribute:")


def _create(name: str, definitions: str, document_types: str, values: str) -> None:
    id = f"{name}_id"
    op.create_table(
        definitions,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("name_key", sa.Text(), nullable=False),
        sa.Column("data_type", sa.Text(), nullable=False),
        sa.Column("is_global", sa.Boolean(), nullable=False),
        sa.Column("choices", types.json_type(), nullable=False),
        sa.Column("created_at", types.UtcDateTime(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{definitions}")),
        sa.UniqueConstraint("name_key", name=op.f(f"uq_{definitions}_name_key")),
    )
    op.create_table(
        document_types,
        sa.Column(id, sa.Uuid(), nullable=False),
        sa.Column("document_type_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            [id],
            [f"{definitions}.id"],
            name=op.f(f"fk_{document_types}_{id}_{definitions}"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_type_id"],
            ["document_types.id"],
            name=op.f(f"fk_{document_types}_document_type_id_document_types"),
        ),
        sa.PrimaryKeyConstraint(id, "document_type_id", name=op.f(f"pk_{document_types}")),
    )
    op.create_table(
        values,
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column(id, sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("value_text", sa.Text(), nullable=True),
        sa.Column("value_decimal", types.ExactDecimal(), nullable=True),
        sa.Column("value_currency", sa.String(length=3), nullable=True),
        sa.Column("value_date", sa.Date(), nullable=True),
        sa.Column("value_boolean", sa.Boolean(), nullable=True),
        sa.ForeignKeyConstraint(
            [id], [f"{definitions}.id"], name=op.f(f"fk_{values}_{id}_{definitions}")
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f(f"fk_{values}_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("document_id", id, name=op.f(f"pk_{values}")),
    )


def _copy(old: tuple[str, str, str], new: tuple[str, str, str], old_id: str, new_id: str) -> None:
    definitions = "id, name, name_key, data_type, is_global, choices, created_at, version"
    values = "kind, value_text, value_decimal, value_currency, value_date, value_boolean"
    op.execute(f"INSERT INTO {new[0]} ({definitions}) SELECT {definitions} FROM {old[0]}")
    op.execute(
        f"INSERT INTO {new[1]} ({new_id}, document_type_id) "
        f"SELECT {old_id}, document_type_id FROM {old[1]}"
    )
    op.execute(
        f"INSERT INTO {new[2]} (document_id, {new_id}, {values}) "
        f"SELECT document_id, {old_id}, {values} FROM {old[2]}"
    )


def _rename_stored(
    keys: dict[str, str], values: dict[str, str], old_prefix: str, new_prefix: str
) -> None:
    def renamer(names: dict[str, str]) -> Callable[[str], str]:
        def rename(text: str) -> str:
            rest = text.removeprefix(old_prefix)
            if rest != text and _UUID.fullmatch(rest):
                return new_prefix + rest
            return names.get(text, text)

        return rename

    rename_key, rename_value = renamer(keys), renamer(values)

    connection = op.get_bind()
    for table_name, column in _STEP_COLUMNS:
        table = sa.table(table_name, sa.column(column, sa.Text()))
        for old, new in keys.items():
            if old.startswith("extract_"):
                connection.execute(
                    table.update().where(table.c[column] == old).values({column: new})
                )
    for table_name, (row_ids, columns) in _JSON_COLUMNS.items():
        table = sa.table(
            table_name,
            *(sa.column(name) for name in row_ids),
            *(sa.column(column, types.json_type()) for column in columns),
        )
        rows = connection.execute(sa.select(table)).mappings().all()
        for row in rows:
            changed = {
                column: new
                for column in columns
                if (new := _walk(row[column], rename_key, rename_value)) != row[column]
            }
            if changed:
                where = sa.and_(*(table.c[name] == row[name] for name in row_ids))
                connection.execute(table.update().where(where).values(changed))


def _walk(value: Any, rename_key: Callable[[str], str], rename_value: Callable[[str], str]) -> Any:
    """`value` with keys and strings renamed; two keys of one object that end up equal are an
    error rather than a silent loss."""
    if isinstance(value, str):
        return rename_value(value)
    if isinstance(value, list):
        return [_walk(item, rename_key, rename_value) for item in value]
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            new_key = rename_key(key)
            if new_key in result:
                raise RuntimeError(f"renaming {key!r} would overwrite {new_key!r}")
            result[new_key] = _walk(item, rename_key, rename_value)
        return result
    return value
