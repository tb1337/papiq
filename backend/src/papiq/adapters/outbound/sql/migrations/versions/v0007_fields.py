"""Attributes are called fields: tables, columns and the stored names

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-09 21:00:00.000000

The tables are created under the new names, filled and the old ones dropped, so constraint
names follow the naming convention on SQLite and Postgres alike. Stored names change by their
place, never by a bare value a person may have written:

- the step `extract_attributes` (step columns, outcome keys, job payloads and dedup keys,
  `step` and `resume_at` values),
- rule conditions `{"field": "attribute", "attribute_id": …}` and the action `set_attribute`,
- field checks named `attribute:<uuid>`,
- the keys `attribute_id` and `attributes` (the answer and metadata in the processing log),
- `attributes` in the changed fields of `document.updated` events.

The downgrade restores the tables and these names, except the key `attributes`: checks were
stored under `fields` before as well, so a key `fields` stays.
"""

import re
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

from papiq.adapters.outbound.sql import types

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STEP = ("extract_attributes", "extract_fields")
_CONDITION_FIELD = ("attribute", "field")
_ACTION = ("set_attribute", "set_field")
_ID_KEY = ("attribute_id", "field_id")
_VALUES_KEY = ("attributes", "fields")
_CHECK_PREFIX = ("attribute:", "field:")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")

# JSON columns that may hold the old names, by table, with the columns that identify a row.
_JSON_COLUMNS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "documents": (("id",), ("processing_outcomes",)),
    "processing_log": (("seq",), ("input", "output")),
    "rule_versions": (("rule_id", "number"), ("conditions", "actions")),
    "rule_applications": (("id",), ("documents", "accept_conflicts", "skipped")),
    "jobs": (("id",), ("payload",)),
    "outbox": (("seq",), ("payload",)),
}
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
    _rename_stored(up=True)


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
    _rename_stored(up=False)


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


def _rename_stored(*, up: bool) -> None:
    def pick(pair: tuple[str, str]) -> tuple[str, str]:
        return pair if up else (pair[1], pair[0])

    step, check = pick(_STEP), pick(_CHECK_PREFIX)
    renamer = _Renamer(
        step=step,
        condition_field=pick(_CONDITION_FIELD),
        action=pick(_ACTION),
        id_key=pick(_ID_KEY),
        values_key=_VALUES_KEY if up else None,
        changed_value=pick(_VALUES_KEY),
        check=check,
    )
    connection = op.get_bind()
    for table_name, column in _STEP_COLUMNS:
        table = sa.table(table_name, sa.column(column, sa.Text()))
        connection.execute(
            table.update().where(table.c[column] == step[0]).values({column: step[1]})
        )
    jobs = sa.table("jobs", sa.column("dedup_key", sa.Text()))
    connection.execute(
        jobs.update()
        .where(jobs.c.dedup_key.like(f"%:{step[0]}"))
        .values(dedup_key=sa.func.replace(jobs.c.dedup_key, f":{step[0]}", f":{step[1]}"))
    )
    marker = "%attribut%" if up else "%field%"
    for table_name, (row_ids, columns) in _JSON_COLUMNS.items():
        table = sa.table(
            table_name,
            *(sa.column(name) for name in row_ids),
            *(sa.column(column, types.json_type()) for column in columns),
            *([sa.column("type", sa.Text())] if table_name == "outbox" else []),
        )
        candidates = sa.or_(
            *(sa.cast(table.c[column], sa.Text()).like(marker) for column in columns)
        )
        rows = connection.execute(sa.select(table).where(candidates)).mappings().all()
        for row in rows:
            changed = {}
            for column in columns:
                new = renamer.walk(row[column])
                if table_name == "outbox" and row["type"] == "document.updated":
                    new = renamer.changed_fields(new)
                if new != row[column]:
                    changed[column] = new
            if changed:
                where = sa.and_(*(table.c[name] == row[name] for name in row_ids))
                connection.execute(table.update().where(where).values(changed))


class _Renamer:
    """Renames by place: keys, step names under `step`/`resume_at`, a condition's field, an
    action's type and check names; any other value stays as it is."""

    def __init__(
        self,
        *,
        step: tuple[str, str],
        condition_field: tuple[str, str],
        action: tuple[str, str],
        id_key: tuple[str, str],
        values_key: tuple[str, str] | None,
        changed_value: tuple[str, str],
        check: tuple[str, str],
    ) -> None:
        self._changed_value = changed_value
        self._step = step
        self._condition_field = condition_field
        self._action = action
        self._check = check
        self._keys = dict([step, id_key, *([values_key] if values_key else [])])

    def walk(self, value: Any) -> Any:
        if isinstance(value, str):
            return self._check_name(value)
        if isinstance(value, list):
            return [self.walk(item) for item in value]
        if not isinstance(value, dict):
            return value
        result: dict[str, Any] = {}
        for key, item in value.items():
            new_key = self._keys.get(key, self._check_name(key))
            if new_key in result or (new_key != key and new_key in value):
                raise RuntimeError(f"renaming {key!r} would overwrite {new_key!r}")
            if key in ("step", "resume_at") and item == self._step[0]:
                item = self._step[1]
            elif key == "field" and "op" in value and item == self._condition_field[0]:
                item = self._condition_field[1]
            elif key == "type" and item == self._action[0]:
                item = self._action[1]
            result[new_key] = self.walk(item)
        return result

    def changed_fields(self, payload: Any) -> Any:
        """`document.updated` names the document's values that changed."""
        if isinstance(payload, dict) and isinstance(payload.get("fields"), list):
            old, new = self._changed_value
            return {
                **payload,
                "fields": [new if name == old else name for name in payload["fields"]],
            }
        return payload

    def _check_name(self, text: str) -> str:
        rest = text.removeprefix(self._check[0])
        return self._check[1] + rest if rest != text and _UUID.fullmatch(rest) else text
