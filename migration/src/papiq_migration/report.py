"""The report of a run: Markdown for people, JSON for machines. It names objects, ids and reasons,
never a key, a token or a password."""

import json
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from papiq_migration.migrate import Migration
from papiq_migration.state import State

KINDS = (
    ("user", "Users"),
    ("group", "Groups"),
    ("contact", "Contacts (Paperless: correspondents)"),
    ("document_type", "Document types"),
    ("tag", "Tags"),
    ("attribute", "Attributes (Paperless: custom fields, ASN, notes)"),
    ("storage_path", "Storage paths"),
    ("drawer", "Drawers"),
    ("other", "Other Paperless objects"),
)


def build(
    command: str, migration: Migration, state: State, *, paperless_url: str, papiq_url: str | None
) -> dict[str, Any]:
    """The report of a trial run or a run, from what the state holds."""
    snapshot = migration.snapshot
    assert snapshot is not None
    objects = {kind: state.objects(kind) for kind, _ in KINDS}
    documents = []
    for row in state.documents():
        if row.source_id not in {int(d["id"]) for d in snapshot.documents}:
            continue
        documents.append(
            {
                "id": row.source_id,
                "title": row.detail.get("title"),
                "owner": row.detail.get("owner"),
                "drawer": row.detail.get("drawer"),
                "status": row.status,
                "papiq_id": row.papiq_id,
                "lane": row.lane,
                "reason": row.reason,
                "notes": row.notes,
                "sha256": row.sha256,
                "upload_seconds": row.upload_seconds,
                "pipeline_seconds": row.pipeline_seconds,
            }
        )
    totals = migration.totals
    return {
        "command": command,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "paperless": {"url": paperless_url, "version": snapshot.version},
        "papiq": {"url": papiq_url},
        "source_counts": {
            "documents": len(snapshot.documents),
            "users": len(snapshot.users),
            "groups": len(snapshot.groups),
            "correspondents": len(snapshot.correspondents),
            "document_types": len(snapshot.document_types),
            "tags": len(snapshot.tags),
            "custom_fields": len(snapshot.custom_fields),
            "storage_paths": len(snapshot.storage_paths),
            **snapshot.counted,
        },
        "results": {
            kind: dict(Counter(item["status"] for item in items)) for kind, items in objects.items()
        },
        "document_results": dict(Counter(item["status"] for item in documents)),
        "lanes": dict(Counter(item["lane"] for item in documents if item["lane"])),
        "totals": {
            "documents": totals.documents,
            "uploaded": totals.uploaded,
            "resumed": totals.resumed,
            "duplicates": totals.duplicates,
            "skipped": totals.skipped,
            "failed": totals.failed,
            "stopped": totals.stopped,
        },
        "seconds": {key: round(value, 1) for key, value in totals.seconds.items()},
        "pipeline_seconds": _stats(
            [
                float(item["pipeline_seconds"])
                for item in documents
                if isinstance(item["pipeline_seconds"], float)
            ]
        ),
        "objects": objects,
        "documents": documents,
    }


def _stats(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    ordered = sorted(values)
    return {
        "count": len(ordered),
        "median": round(ordered[len(ordered) // 2], 1),
        "p90": round(ordered[int(len(ordered) * 0.9)], 1),
        "max": round(ordered[-1], 1),
    }


def to_json(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def to_markdown(report: dict[str, Any]) -> str:
    out: list[str] = []
    command = report["command"]
    titles = {"plan": "Trial run", "run": "Migration", "verify": "Verification"}
    out.append(f"# Paperless-ngx to Papiq: {titles[command]}")
    out.append("")
    out.append(
        f"{report['generated_at']} · Paperless {report['paperless']['version']} "
        f"({report['paperless']['url']}) · Papiq {report['papiq']['url'] or 'not read'}"
    )
    out.append("")
    if command == "verify":
        out += _verification(report)
    else:
        out += _summary(report)
    return "\n".join(out) + "\n"


def _table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(_cell(cell) for cell in row) + " |")
    return [*lines, ""]


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        value = "; ".join(str(item) for item in value)
    return str(value).replace("|", "\\|").replace("\n", " ")


def _summary(report: dict[str, Any]) -> list[str]:
    out: list[str] = ["## Summary", ""]
    rows = []
    for kind, title in KINDS:
        results = report["results"][kind]
        if results:
            rows.append(
                [
                    title,
                    sum(results.values()),
                    ", ".join(f"{k} {v}" for k, v in sorted(results.items())),
                ]
            )
    results = report["document_results"]
    rows.append(
        [
            "Documents",
            sum(results.values()),
            ", ".join(f"{k} {v}" for k, v in sorted(results.items())),
        ]
    )
    out += _table(["Kind", "Count", "Result"], rows)
    if report["lanes"]:
        out.append("Lanes: " + ", ".join(f"{k} {v}" for k, v in sorted(report["lanes"].items())))
        out.append("")
    out.append("Times (seconds): " + ", ".join(f"{k} {v}" for k, v in report["seconds"].items()))
    if report["pipeline_seconds"]:
        stats = report["pipeline_seconds"]
        out.append(
            f"Pipeline per document (upload to finished, seconds): median {stats['median']}, "
            f"90th percentile {stats['p90']}, longest {stats['max']} ({stats['count']} documents)."
        )
    out.append("")

    problems = [d for d in report["documents"] if d["status"] in ("failed", "uploaded")]
    out += _section("Documents that failed or are not finished", problems)
    skipped = [d for d in report["documents"] if d["status"] == "skipped"]
    out += _section("Documents not taken over", skipped)
    not_green = [d for d in report["documents"] if d["lane"] in ("yellow", "red")]
    out += _section("Documents in yellow or red", not_green)
    remarks = [d for d in report["documents"] if d["notes"]]
    if remarks:
        out += ["## Remarks on documents", ""]
        out += _table(
            ["Paperless", "Title", "Remarks"], [[d["id"], d["title"], d["notes"]] for d in remarks]
        )

    for kind, title in KINDS:
        items = report["objects"][kind]
        if not items:
            continue
        out += [f"## {title}", ""]
        out += _table(
            ["Paperless", "Name", "Papiq", "Result", "Remarks"],
            [[i["source_id"], i["name"], i["papiq_id"], i["status"], i["notes"]] for i in items],
        )
    out += ["## All documents", ""]
    out += _table(
        ["Paperless", "Title", "Owner", "Drawer", "Papiq", "Result", "Lane"],
        [
            [
                d["id"],
                d["title"],
                d["owner"] or "(executing admin)",
                d["drawer"],
                d["papiq_id"],
                d["status"],
                d["lane"],
            ]
            for d in report["documents"]
        ],
    )
    return out


def _section(title: str, documents: list[dict[str, Any]]) -> list[str]:
    if not documents:
        return []
    return [
        f"## {title} ({len(documents)})",
        "",
        *_table(
            ["Paperless", "Title", "Papiq", "Reason"],
            [[d["id"], d["title"], d["papiq_id"], d["reason"]] for d in documents],
        ),
    ]


def _verification(report: dict[str, Any]) -> list[str]:
    deviations = report["deviations"]
    out = ["## Result", ""]
    out.append(
        f"{report['checked']} documents and {report['objects_checked']} other objects compared; "
        + ("no deviation." if not deviations else f"{len(deviations)} deviations.")
    )
    out.append("")
    if deviations:
        out += _table(
            ["Object", "Id", "What", "Paperless", "Papiq"],
            [
                [d["kind"], d["id"], d["what"], d.get("expected"), d.get("found")]
                for d in deviations
            ],
        )
    if report["omitted"]:
        out += ["## Not taken over, as decided", ""]
        out += _table(["Kind", "Count"], [[k, v] for k, v in sorted(report["omitted"].items())])
    if report["findings"]:
        out += ["## Documents in yellow or red", ""]
        out += _table(
            ["Paperless", "Title", "Lane", "Reason"],
            [[d["id"], d["title"], d["lane"], d["reason"]] for d in report["findings"]],
        )
    out.append("Times (seconds): " + ", ".join(f"{k} {v}" for k, v in report["seconds"].items()))
    out.append("")
    return out
