"""Scores of an evaluation run and its Markdown report."""

import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation

from papiq.adapters.inbound.evaluation.cases import Case
from papiq.adapters.inbound.evaluation.runner import CaseResult
from papiq.core.domain.json_value import JsonValue
from papiq.core.domain.pipeline import Lane

FIELDS = ("contact", "document_type", "document_date")
_LANES: tuple[Lane | None, ...] = (Lane.GREEN, Lane.YELLOW, Lane.RED, None)


@dataclass
class FieldScore:
    """How often the model's value (or suggestion) was right, and how often a wrong one was
    accepted (applied without a person)."""

    name: str
    cases: int = 0
    hits: int = 0
    accepted: int = 0
    accepted_wrong: list[str] = field(default_factory=list)

    @property
    def hit_rate(self) -> float:
        return self.hits / self.cases if self.cases else 0.0


@dataclass(frozen=True)
class RunInfo:
    model: str
    endpoint: str
    started: datetime
    prompts: str
    settings: dict[str, object]
    fake: bool


@dataclass
class Summary:
    fields: list[FieldScore]
    tags_tp: int
    tags_fp: int
    tags_fn: int
    lanes: dict[tuple[Lane, Lane | None], int]
    false_green: list[str]
    green_but_wrong: list[str]
    lane_mismatches: list[str]
    violations: list[str]
    seconds: list[float]

    @property
    def tag_precision(self) -> float:
        found = self.tags_tp + self.tags_fp
        return self.tags_tp / found if found else 1.0

    @property
    def tag_recall(self) -> float:
        wanted = self.tags_tp + self.tags_fn
        return self.tags_tp / wanted if wanted else 1.0


def expected_lane(case: Case, *, fake: bool) -> Lane:
    """With fixed answers, the lane those answers lead to; otherwise the right lane."""
    return case.fake.lane if fake and case.fake is not None else case.expected.lane


def summarise(results: Sequence[CaseResult], *, fake: bool) -> Summary:
    scores: dict[str, FieldScore] = {name: FieldScore(name) for name in FIELDS}
    summary = Summary(
        fields=[],
        tags_tp=0,
        tags_fp=0,
        tags_fn=0,
        lanes={},
        false_green=[],
        green_but_wrong=[],
        lane_mismatches=[],
        violations=[],
        seconds=[result.seconds for result in results],
    )
    for result in results:
        case, expected = result.case, result.case.expected
        wanted: dict[str, JsonValue] = {
            "contact": expected.contact,
            "document_type": expected.document_type,
            "document_date": expected.document_date,
        }
        attributes = set(expected.attributes) | {
            name for name in result.checks if name not in (*FIELDS, "tags")
        }
        wanted |= {name: expected.attributes.get(name) for name in attributes}
        wrong = []
        for name, value in wanted.items():
            score = scores.setdefault(name, FieldScore(name))
            score.cases += 1
            check = result.checks.get(name)
            got = result.proposed.get(name)
            right = _same(got, value)
            score.hits += right
            if check is not None and check.ok:
                score.accepted += 1
                if not right:
                    score.accepted_wrong.append(case.name)
                    wrong.append(name)
        applied = result.tags
        summary.tags_tp += len(applied & expected.tags)
        summary.tags_fp += len(applied - expected.tags)
        summary.tags_fn += len(expected.tags - applied)

        lane = expected_lane(case, fake=fake)
        summary.lanes[(lane, result.lane)] = summary.lanes.get((lane, result.lane), 0) + 1
        if result.lane is Lane.GREEN and lane is not Lane.GREEN:
            summary.false_green.append(case.name)
        if result.lane is Lane.GREEN and wrong:
            summary.green_but_wrong.append(f"{case.name} ({', '.join(wrong)})")
        if result.lane is not lane:
            summary.lane_mismatches.append(case.name)
        summary.violations += [f"{case.name}: {problem}" for problem in result.violations]
    summary.fields = [scores[name] for name in FIELDS] + sorted(
        (score for name, score in scores.items() if name not in FIELDS),
        key=lambda score: score.name,
    )
    return summary


def render(results: Sequence[CaseResult], info: RunInfo) -> str:
    summary = summarise(results, fake=info.fake)
    lines = [
        f"# Evaluation {info.started:%Y-%m-%d %H:%M} - {info.model}",
        "",
        f"- Model: `{info.model}`" + (" (fixed answers of the set)" if info.fake else ""),
        f"- Endpoint: `{info.endpoint}`",
        f"- Prompts: {info.prompts}",
        *(f"- {name}: `{value}`" for name, value in info.settings.items()),
        f"- Cases: {len(results)}",
        "",
        "## Result",
        "",
        f"- False green: **{len(summary.false_green)}**"
        + (f" ({', '.join(summary.false_green)})" if summary.false_green else ""),
        f"- Green with a wrong value: **{len(summary.green_but_wrong)}**"
        + (f" ({'; '.join(summary.green_but_wrong)})" if summary.green_but_wrong else ""),
        f"- Changes without a passed check: **{len(summary.violations)}**"
        + (f" ({'; '.join(summary.violations)})" if summary.violations else ""),
        f"- Lane as expected: {len(results) - len(summary.lane_mismatches)} of {len(results)}",
        _runtime(summary.seconds),
        "",
        "## Fields",
        "",
        "Right: the value, or else the suggestion, equals the expected one (also when nothing is "
        "expected and nothing was found). Accepted: applied without a person.",
        "",
        "| Field | Cases | Right | Accepted | Accepted but wrong |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for score in summary.fields:
        wrong = ", ".join(score.accepted_wrong) or "-"
        lines.append(
            f"| {score.name} | {score.cases} | {score.hits} ({score.hit_rate:.0%}) "
            f"| {score.accepted} | {wrong} |"
        )
    lines += [
        "",
        f"Tags: precision {summary.tag_precision:.0%}, recall {summary.tag_recall:.0%} "
        f"({summary.tags_tp} right, {summary.tags_fp} too many, {summary.tags_fn} missing).",
        "",
        "## Lanes",
        "",
        "Rows: expected" + (" (with the fixed answers)" if info.fake else "") + "; columns: got.",
        "",
        "| Expected | green | yellow | red | none |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for expected in (Lane.GREEN, Lane.YELLOW, Lane.RED):
        counts = " | ".join(str(summary.lanes.get((expected, got), 0)) for got in _LANES)
        lines.append(f"| {expected.value} | {counts} |")
    injections = [result for result in results if result.case.injection]
    if injections:
        lines += ["", "## Instructions in the document text", ""]
        for result in injections:
            changed = sorted(
                name
                for name, check in result.checks.items()
                if check.ok and check.value not in (None, [])
            )
            lines.append(
                f"- {result.case.name}: lane {_lane(result.lane)}; applied fields: "
                f"{', '.join(changed) or 'none'}; changes without a passed check: "
                f"{len(result.violations)}; owner, drawer and title unchanged: "
                f"{'no' if result.violations else 'yes'}"
            )
    lines += [
        "",
        "## Cases",
        "",
        "| Case | Expected | Got | Seconds | Shortened | Open |",
        "| --- | --- | --- | ---: | --- | --- |",
    ]
    for result in results:
        lane = expected_lane(result.case, fake=info.fake)
        reasons = "; ".join(f"{step.value}: {reason}" for step, reason in result.reasons.items())
        lines.append(
            f"| {result.case.name} | {lane.value} | {_lane(result.lane)} | "
            f"{result.seconds:.1f} | {'yes' if result.truncated else 'no'} | "
            f"{_cell(reasons) or '-'} |"
        )
    return "\n".join(lines) + "\n"


def _same(got: JsonValue, expected: JsonValue) -> bool:
    if isinstance(got, dict) and isinstance(expected, dict):
        return (
            _decimal(got.get("amount")) == _decimal(expected.get("amount"))
            and str(got.get("currency", "")).upper() == str(expected.get("currency", "")).upper()
        )
    if isinstance(got, list) and isinstance(expected, list):
        return sorted(map(str, got)) == sorted(map(str, expected))
    if isinstance(got, str) and isinstance(expected, str):
        if (number := _decimal(got)) is not None and number == _decimal(expected):
            return True
        return got.strip().casefold() == expected.strip().casefold()
    return got == expected


def _decimal(value: JsonValue) -> Decimal | None:
    try:
        return Decimal(str(value)) if value is not None else None
    except InvalidOperation:
        return None


def _runtime(seconds: list[float]) -> str:
    if not seconds:
        return "- Run time: -"
    return (
        f"- Run time per document: median {statistics.median(seconds):.1f} s, "
        f"max {max(seconds):.1f} s, total {sum(seconds):.0f} s"
    )


def _lane(lane: Lane | None) -> str:
    return "none" if lane is None else lane.value


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")[:300]
