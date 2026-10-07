"""The Markdown report of a search evaluation."""

from papiq.adapters.inbound.evaluation.search import (
    KINDS_AT,
    ModelResult,
    QuerySet,
    RatioResult,
    SearchRun,
    SearchRunInfo,
)


def render_search(run: SearchRun, queries: QuerySet, info: SearchRunInfo) -> str:
    names = ", ".join(f"`{model.name}`" for model in run.models)
    lines = [
        f"# Search evaluation {info.started:%Y-%m-%d %H:%M} - {names}",
        "",
        f"- Embedding endpoint: `{info.endpoint}`",
        f"- Index: {info.index}",
        *(f"- {name}: `{value}`" for name, value in info.settings.items()),
        f"- Documents: {run.documents}, queries: {len(queries.cases)}",
        "- Result per query: the first expected case among the first 10 hits. Hit@k: the share of "
        "queries found within the first k hits; MRR: mean of 1/rank (0 if not found).",
    ]
    if info.fake:
        lines += [
            "",
            "> Fake run: bag-of-words vectors and the in-memory index. It shows that the "
            "evaluation works, not how good a model is.",
        ]
    lines += ["", "## Results", ""]
    lines += [
        "| Model | Ratio | Hit@1 | Hit@3 | Hit@5 | MRR | Mean ms | p95 ms | Without meaning |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for model in run.models:
        for ratio in model.ratios:
            lines.append(
                f"| {model.name} | {ratio.ratio:g} | {ratio.rate(1):.0%} | {ratio.rate(3):.0%} "
                f"| {ratio.rate(5):.0%} | {ratio.mrr:.3f} | {ratio.mean_ms:.0f} "
                f"| {ratio.p95_ms:.0f} | {ratio.without_meaning} |"
            )
    lines += [
        "",
        "Ratio 0 is full text only, 1 is meaning only. 'Without meaning': queries of a ratio "
        "above 0 for which the embedding of the query failed, so only the words counted.",
        "",
        f"## Hit@{KINDS_AT} by kind of query",
        "",
        f"| Model | Ratio | {' | '.join(_kind(queries, kind) for kind in queries.kinds)} |",
        f"| --- | ---: | {' | '.join('---:' for _ in queries.kinds)} |",
    ]
    for model in run.models:
        for ratio in model.ratios:
            cells = " | ".join(
                f"{ratio.rate(KINDS_AT, kind):.0%}" if ratio.count(kind) else "-"
                for kind in queries.kinds
            )
            lines.append(f"| {model.name} | {ratio.ratio:g} | {cells} |")
    lines += [
        "",
        "## Cost of indexing",
        "",
        "| Model | Dimensions | Sections | Per document | Embedding s | Rebuild s "
        "| s per section |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for model in run.models:
        lines.append(_cost(model, run.documents))
    lines += ["", "## Misses", ""]
    for model in run.models:
        nearest = _nearest(model.ratios, info.default_ratio)
        if nearest is None:
            continue
        lines += [f"### {model.name}, ratio {nearest.ratio:g}", ""]
        missed = [o for o in nearest.outcomes if o.rank is None or o.rank > KINDS_AT]
        if not missed:
            lines += [f"Every query is within the first {KINDS_AT} hits.", ""]
            continue
        lines += [
            "| Query | Kind | Expected | Rank | First hits |",
            "| --- | --- | --- | ---: | --- |",
        ]
        for outcome in missed:
            lines.append(
                f"| {_cell(outcome.case.query)} | {outcome.case.kind} "
                f"| {_cell(', '.join(sorted(outcome.case.expected)))} "
                f"| {'-' if outcome.rank is None else outcome.rank} "
                f"| {_cell(', '.join(outcome.got[:3]) or 'none')} |"
            )
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def _cost(model: ModelResult, documents: int) -> str:
    if not model.sections:
        return f"| {model.name} | - | - | - | - | {model.index_seconds:.1f} | - |"
    return (
        f"| {model.name} | {model.dimensions} | {model.sections} "
        f"| {model.sections / documents:.1f} | {model.embed_seconds:.1f} "
        f"| {model.index_seconds:.1f} | {model.embed_seconds / model.sections:.2f} |"
    )


def _kind(queries: QuerySet, kind: str) -> str:
    count = sum(1 for case in queries.cases if case.kind == kind)
    return f"{kind} ({count})"


def _nearest(ratios: list[RatioResult], wanted: float) -> RatioResult | None:
    return min(ratios, key=lambda ratio: abs(ratio.ratio - wanted), default=None)


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")[:200]
