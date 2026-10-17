"""Scores of an evaluation's runs and their Markdown report."""

import statistics
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation

from papiq.adapters.inbound.evaluation.cases import Case, Run
from papiq.adapters.inbound.evaluation.runner import CaseResult
from papiq.core.domain.json_value import JsonValue
from papiq.core.domain.pipeline import Lane

FIELDS = ("contact", "document_type", "topic", "document_date")
_APPLIED_ONLY = frozenset({"topic"})
"""Fields always checked OK, set or only suggested: accepted only when a value was applied."""
_LANES: tuple[Lane | None, ...] = (Lane.GREEN, Lane.YELLOW, Lane.RED, None)


@dataclass
class FieldScore:
    """How often the value (or suggestion) was right, how often a wrong one was accepted
    (applied without a person), and how many of the accepted ones the person corrected
    afterwards (the log's `corrected`)."""

    name: str
    cases: int = 0
    hits: int = 0
    accepted: int = 0
    accepted_wrong: list[str] = field(default_factory=list)
    corrected: int = 0

    @property
    def hit_rate(self) -> float:
        return self.hits / self.cases if self.cases else 0.0

    @property
    def corrected_share(self) -> float:
        """The feedback measure: the share of accepted values a person corrected."""
        return self.corrected / self.accepted if self.accepted else 0.0


@dataclass(frozen=True)
class RunInfo:
    model: str
    endpoint: str
    started: datetime
    prompts: str
    settings: dict[str, object]
    fake: bool
    training: int = 0  # documents of the training corpus


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
    tags_set: int = 0  # documents tags were set on without a person
    tags_corrected: int = 0  # of those, the person changed the tags afterwards

    @property
    def tag_precision(self) -> float:
        found = self.tags_tp + self.tags_fp
        return self.tags_tp / found if found else 1.0

    @property
    def tag_recall(self) -> float:
        wanted = self.tags_tp + self.tags_fn
        return self.tags_tp / wanted if wanted else 1.0


def expected_lane(case: Case, *, fake: bool, run: Run = Run.COLD) -> Lane:
    """With fixed answers, the lane those answers lead to in `run`; otherwise the right lane."""
    if fake and case.fake is not None:
        return case.fake.lane_in(run)
    return case.expected.lane_in(run)


def summarise(results: Sequence[CaseResult], *, fake: bool) -> Summary:
    """The scores of one run's results."""
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
            "topic": expected.topic,
            "document_date": expected.document_date,
        }
        # The title is no field of the set: nothing is expected of it.
        checked = {name for name in result.checks if name not in (*FIELDS, "tags", "title")}
        fields = (set(expected.fields) | checked) - result.by_hand
        wanted |= {name: expected.fields.get(name) for name in fields}
        wrong = []
        for name, value in wanted.items():
            score = scores.setdefault(name, FieldScore(name))
            score.cases += 1
            check = result.checks.get(name)
            got = result.proposed.get(name)
            right = _same(got, value)
            score.hits += right
            if (
                check is not None
                and check.ok
                and (name not in _APPLIED_ONLY or check.value is not None)
            ):
                score.accepted += 1
                score.corrected += name in result.corrected
                if not right:
                    score.accepted_wrong.append(case.name)
                    wrong.append(name)
        applied = result.tags
        if "tags" in result.automatic and applied:
            summary.tags_set += 1
            summary.tags_corrected += "tags" in result.corrected
        summary.tags_tp += len(applied & expected.tags)
        summary.tags_fp += len(applied - expected.tags)
        summary.tags_fn += len(expected.tags - applied)

        lane = expected_lane(case, fake=fake, run=result.run)
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


_RUN_TITLES = {Run.COLD: "cold", Run.TRAINED: "trained"}


def render(runs: Mapping[Run, Sequence[CaseResult]], info: RunInfo) -> str:
    """The report of the runs: their results and fields side by side, then each run's lanes
    and cases."""
    summaries = {run: summarise(results, fake=info.fake) for run, results in runs.items()}
    titles = [_RUN_TITLES[run] for run in runs]
    lines = [
        f"# Evaluation {info.started:%Y-%m-%d %H:%M} - {info.model}",
        "",
        f"- Model: `{info.model}`" + (" (fixed answers of the set)" if info.fake else ""),
        f"- Endpoint: `{info.endpoint}`",
        f"- Prompts: {info.prompts}",
        *(f"- {name}: `{value}`" for name, value in info.settings.items()),
        "- Runs: "
        + ", ".join(f"{_RUN_TITLES[run]} ({len(results)} cases)" for run, results in runs.items())
        + (
            f"; the trained one took over {info.training} documents of the training corpus first"
            if Run.TRAINED in runs
            else ""
        ),
        "",
        "## Result",
        "",
        "| | " + " | ".join(titles) + " |",
        "| --- |" + " ---: |" * len(titles),
    ]

    def row(name: str, value: Callable[[Run, Summary], str]) -> str:
        return (
            f"| {name} | " + " | ".join(value(run, item) for run, item in summaries.items()) + " |"
        )

    lines += [
        row("False green", lambda _, item: f"**{len(item.false_green)}**"),
        row("Green with a wrong value", lambda _, item: f"**{len(item.green_but_wrong)}**"),
        row("Changes without a passed check", lambda _, item: f"**{len(item.violations)}**"),
        row(
            "Lane as expected",
            lambda run, item: f"{len(runs[run]) - len(item.lane_mismatches)} of {len(runs[run])}",
        ),
        row("Run time per document (median)", lambda _, item: _median(item.seconds)),
        "",
    ]
    for run, item in summaries.items():
        for what, names in (
            ("False green", item.false_green),
            ("Green with a wrong value", item.green_but_wrong),
            ("Changes without a passed check", item.violations),
        ):
            if names:
                lines.append(f"- {what} ({_RUN_TITLES[run]}): {'; '.join(names)}")
    lines += [
        "",
        "## Fields",
        "",
        "Right: the value, or else the suggestion, equals the expected one (also when nothing is "
        "expected and nothing was found). Accepted: applied without a person. Corrected: the "
        "share of accepted values the person changed afterwards (the feedback measure). The "
        "fields of a type the judge left uncertain are read once a person decided it: until "
        "then they count as not found.",
        "",
        "| Field | Cases | "
        + " | ".join(
            f"Right ({title}) | Accepted ({title}) | Corrected ({title})" for title in titles
        )
        + " | Accepted but wrong |",
        "| --- | ---: |" + " ---: | ---: | ---: |" * len(titles) + " --- |",
    ]
    names = list(dict.fromkeys(score.name for item in summaries.values() for score in item.fields))
    for name in names:
        scores = [_score(item, name) for item in summaries.values()]
        cases = max(score.cases for score in scores)
        cells = " | ".join(
            f"{score.hits} ({score.hit_rate:.0%}) | {score.accepted} | {score.corrected_share:.0%}"
            for score in scores
        )
        wrong = "; ".join(
            f"{_RUN_TITLES[run]}: {', '.join(score.accepted_wrong)}"
            for run, score in zip(summaries, scores, strict=True)
            if score.accepted_wrong
        )
        lines.append(f"| {name} | {cases} | {cells} | {wrong or '-'} |")
    lines.append("")
    for run, item in summaries.items():
        lines.append(
            f"Tags ({_RUN_TITLES[run]}): precision {item.tag_precision:.0%}, recall "
            f"{item.tag_recall:.0%} ({item.tags_tp} right, {item.tags_fp} too many, "
            f"{item.tags_fn} missing); corrected afterwards on {item.tags_corrected} of "
            f"{item.tags_set} documents they were set on."
        )
    for run, results in runs.items():
        lines += _run_section(run, results, summaries[run], info)
    return "\n".join(lines) + "\n"


def _run_section(
    run: Run, results: Sequence[CaseResult], summary: Summary, info: RunInfo
) -> list[str]:
    title = _RUN_TITLES[run]
    lines = [
        "",
        f"## Lanes ({title})",
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
        lines += ["", f"## Instructions in the document text ({title})", ""]
        for result in injections:
            changed = sorted(
                name
                for name, check in result.checks.items()
                if check.ok and check.value not in (None, [])
            )
            lines.append(
                f"- {result.case.name}: lane {_lane(result.lane)}; applied fields: "
                f"{', '.join(changed) or 'none'}; changes without a passed check: "
                f"{len(result.violations)}; owner and space unchanged, title only as "
                f"checked: {'no' if result.violations else 'yes'}"
            )
    lines += [
        "",
        f"## Cases ({title})",
        "",
        "| Case | Expected | Got | Seconds | Shortened | Corrected | Open |",
        "| --- | --- | --- | ---: | --- | --- | --- |",
    ]
    for result in results:
        lane = expected_lane(result.case, fake=info.fake, run=run)
        reasons = "; ".join(f"{node.value}: {reason}" for node, reason in result.reasons.items())
        corrected = ", ".join(sorted(result.corrected & result.automatic)) or "-"
        lines.append(
            f"| {result.case.name} | {lane.value} | {_lane(result.lane)} | "
            f"{result.seconds:.1f} | {'yes' if result.truncated else 'no'} | {corrected} | "
            f"{_cell(reasons) or '-'} |"
        )
    return lines


def _score(summary: Summary, name: str) -> FieldScore:
    return next((score for score in summary.fields if score.name == name), FieldScore(name))


def _median(seconds: list[float]) -> str:
    return f"{statistics.median(seconds):.1f} s" if seconds else "-"


def _same(got: JsonValue, expected: JsonValue) -> bool:
    if isinstance(got, dict) and isinstance(expected, dict) and "start" in expected:
        return (got.get("start"), got.get("end")) == (expected.get("start"), expected.get("end"))
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


def _lane(lane: Lane | None) -> str:
    return "none" if lane is None else lane.value


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")[:300]
