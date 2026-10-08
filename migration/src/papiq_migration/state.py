"""The local state of a migration: which Paperless object became which Papiq object, and how far
each document got. A SQLite file next to the run, so an interrupted migration continues where it
stopped and a second run creates nothing twice. It holds no secrets."""

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS objects (
    kind TEXT NOT NULL,
    source_id TEXT NOT NULL,
    name TEXT NOT NULL,
    papiq_id TEXT,
    status TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (kind, source_id)
);
CREATE TABLE IF NOT EXISTS documents (
    source_id INTEGER PRIMARY KEY,
    status TEXT NOT NULL,
    papiq_id TEXT,
    sha256 TEXT,
    lane TEXT,
    reason TEXT,
    notes TEXT NOT NULL DEFAULT '[]',
    detail TEXT NOT NULL DEFAULT '{}',
    upload_seconds REAL,
    pipeline_seconds REAL,
    updated_at TEXT NOT NULL
);
"""
# Document states: `uploaded` (accepted, pipeline not finished), `done` (pipeline finished),
# `duplicate` (Papiq had the file already), `skipped` (cannot be taken over), `failed`.
DOCUMENT_DONE = ("done", "duplicate", "skipped")


class StateError(Exception):
    pass


@dataclass(frozen=True)
class DocumentRow:
    source_id: int
    status: str
    papiq_id: str | None
    sha256: str | None
    lane: str | None
    reason: str | None
    notes: list[str]
    detail: dict[str, Any]
    upload_seconds: float | None
    pipeline_seconds: float | None


class State:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path)
        self._db.executescript(SCHEMA)
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    def bind(self, **identity: str) -> None:
        """Remember what the state belongs to; a different source or target is refused, so two
        migrations never mix."""
        for key, value in identity.items():
            row = self._db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
            if row is None:
                self._db.execute("INSERT INTO meta VALUES (?, ?)", (key, value))
            elif row[0] != value:
                raise StateError(
                    f"this state file belongs to another {key} ({row[0]}); use another --state"
                )
        self._db.commit()

    # --- objects -----------------------------------------------------------------------------

    def object(self, kind: str, source_id: str) -> tuple[str | None, str] | None:
        row = self._db.execute(
            "SELECT papiq_id, status FROM objects WHERE kind = ? AND source_id = ?",
            (kind, source_id),
        ).fetchone()
        return None if row is None else (row[0], row[1])

    def put_object(
        self,
        kind: str,
        source_id: str,
        name: str,
        papiq_id: str | None,
        status: str,
        notes: list[str],
    ) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO objects VALUES (?, ?, ?, ?, ?, ?)",
            (kind, source_id, name, papiq_id, status, json.dumps(notes)),
        )
        self._db.commit()

    def objects(self, kind: str) -> list[dict[str, Any]]:
        rows = self._db.execute(
            "SELECT source_id, name, papiq_id, status, notes FROM objects WHERE kind = ? "
            "ORDER BY rowid",
            (kind,),
        ).fetchall()
        return [
            {
                "source_id": r[0],
                "name": r[1],
                "papiq_id": r[2],
                "status": r[3],
                "notes": json.loads(r[4]),
            }
            for r in rows
        ]

    # --- documents ---------------------------------------------------------------------------

    def document(self, source_id: int) -> DocumentRow | None:
        rows = self._select("WHERE source_id = ?", (source_id,))
        return rows[0] if rows else None

    def documents(self) -> list[DocumentRow]:
        return self._select("ORDER BY source_id", ())

    def put_document(
        self,
        source_id: int,
        status: str,
        *,
        papiq_id: str | None = None,
        sha256: str | None = None,
        lane: str | None = None,
        reason: str | None = None,
        notes: list[str] | None = None,
        detail: dict[str, Any] | None = None,
        upload_seconds: float | None = None,
        pipeline_seconds: float | None = None,
    ) -> None:
        before = self.document(source_id)
        self._db.execute(
            "INSERT OR REPLACE INTO documents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                source_id,
                status,
                papiq_id or (before.papiq_id if before else None),
                sha256 or (before.sha256 if before else None),
                lane,
                reason,
                json.dumps(notes if notes is not None else (before.notes if before else [])),
                json.dumps(detail if detail is not None else (before.detail if before else {})),
                upload_seconds
                if upload_seconds is not None
                else (before.upload_seconds if before else None),
                pipeline_seconds
                if pipeline_seconds is not None
                else (before.pipeline_seconds if before else None),
                datetime.now(UTC).isoformat(timespec="seconds"),
            ),
        )
        self._db.commit()

    def _select(self, where: str, args: tuple[Any, ...]) -> list[DocumentRow]:
        rows = self._db.execute(
            "SELECT source_id, status, papiq_id, sha256, lane, reason, notes, detail, "
            f"upload_seconds, pipeline_seconds FROM documents {where}",
            args,
        ).fetchall()
        return [
            DocumentRow(
                r[0], r[1], r[2], r[3], r[4], r[5], json.loads(r[6]), json.loads(r[7]), r[8], r[9]
            )
            for r in rows
        ]
