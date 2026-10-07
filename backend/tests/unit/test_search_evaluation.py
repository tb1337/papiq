"""The search evaluation: queries on the evaluation set, run with fake embeddings."""

import json
import shutil
from collections.abc import Mapping
from functools import partial
from pathlib import Path

import pytest

from papiq.adapters.inbound.evaluation import EvaluationSetError, load_queries, load_set
from papiq.adapters.inbound.evaluation.search import QueryOutcome, RatioResult, SearchCase
from papiq.composition.__main__ import main
from papiq.composition.errors import ConfigurationError
from papiq.composition.search_evaluation import parse_ratios, run_search_evaluation
from papiq.composition.settings import Settings

SET = Path(__file__).parents[2] / "evaluation"


def write_queries(path: Path, *queries: Mapping[str, object]) -> Path:
    path.write_text(json.dumps({"queries": list(queries)}), encoding="utf-8")
    return path


async def test_the_fake_run_finds_by_words_and_writes_a_report(tmp_path: Path) -> None:
    report = tmp_path / "report.md"
    result = await run_search_evaluation(
        Settings.model_construct(), cases=SET, fake=True, output=report
    )
    (model,) = result.models
    assert [ratio.ratio for ratio in model.ratios] == [0.0, 0.5, 1.0]
    assert model.dimensions == 256
    assert model.sections >= result.run.documents

    # Words in the documents are found by the words alone. This also catches a typo in the
    # expected case names of the queries.
    words = model.ratios[0]
    for kind in ("exact", "number", "late"):
        assert words.rate(3, kind) == 1.0, kind
    assert all(not outcome.semantic for outcome in words.outcomes)
    assert all(outcome.semantic for outcome in model.ratios[1].outcomes)

    text = report.read_text(encoding="utf-8")
    for heading in ("## Results", "## Hit@3 by kind of query", "## Cost of indexing", "## Misses"):
        assert heading in text
    assert "Fake run" in text


async def test_a_document_is_not_found_by_the_name_of_its_case(tmp_path: Path) -> None:
    queries = write_queries(
        tmp_path / "queries.json",
        {"query": "01-stromrechnung", "kind": "exact", "expected": ["01-stromrechnung"]},
        {"query": "scan_0001", "kind": "exact", "expected": ["01-stromrechnung"]},
    )
    result = await run_search_evaluation(
        Settings.model_construct(),
        cases=SET,
        queries=queries,
        fake=True,
        ratios=[0.0],
        output=tmp_path / "r.md",
    )
    by_name, by_number = result.models[0].ratios[0].outcomes
    assert by_name.rank is None  # the name of the case is not in the index
    assert by_number.rank == 1  # the title is a number instead


def test_the_queries_are_checked(tmp_path: Path) -> None:
    known = frozenset(case.name for case in load_set(SET).cases)
    assert load_queries(SET / "search" / "queries.json", known).cases
    good = {"query": "Strom", "kind": "exact", "expected": ["01-stromrechnung"]}
    with pytest.raises(EvaluationSetError, match="cannot read"):
        load_queries(tmp_path / "missing.json", known)
    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    with pytest.raises(EvaluationSetError, match="not valid JSON"):
        load_queries(broken, known)
    with pytest.raises(EvaluationSetError, match="no queries"):
        load_queries(write_queries(tmp_path / "empty.json"), known)
    for bad, message in (
        ({**good, "query": " "}, "no query text"),
        ({**good, "kind": ""}, "no kind"),
        ({**good, "expected": []}, "expects no case"),
        ({**good, "expected": ["99-nothing"]}, "unknown cases: 99-nothing"),
    ):
        with pytest.raises(EvaluationSetError, match=message):
            load_queries(write_queries(tmp_path / "bad.json", good, bad), known)


def test_the_measures() -> None:
    def outcome(rank: int | None, seconds: float, kind: str = "exact") -> QueryOutcome:
        case = SearchCase("q", kind, frozenset({"a"}))
        return QueryOutcome(case, rank, (), seconds, True)

    result = RatioResult(
        0.5, [outcome(1, 0.1), outcome(2, 0.2), outcome(5, 0.3, "typo"), outcome(None, 0.4)]
    )
    assert (result.found(1), result.found(3), result.found(5)) == (1, 2, 3)
    assert result.rate(3) == 0.5
    assert result.rate(3, "typo") == 0.0
    assert result.count("typo") == 1
    assert result.mrr == pytest.approx((1 + 1 / 2 + 1 / 5) / 4)
    assert result.mean_ms == pytest.approx(250)
    assert result.p95_ms == pytest.approx(400)
    assert RatioResult(0.0).mrr == 0.0
    assert RatioResult(0.0).p95_ms == 0.0


def test_ratios() -> None:
    assert parse_ratios("0, 0.5,1,0.5") == (0.0, 0.5, 1.0)
    for bad in ("", "a", "0,2", "-0.1"):
        with pytest.raises(ConfigurationError, match="--ratios"):
            parse_ratios(bad)


async def test_a_real_run_needs_meilisearch_embeddings_and_a_model(tmp_path: Path) -> None:
    run = partial(run_search_evaluation, cases=SET, output=tmp_path / "r.md")
    with pytest.raises(ConfigurationError, match="PAPIQ_MEILISEARCH_URL"):
        await run(Settings.model_construct())
    settings = Settings.model_construct(meilisearch_url="http://meilisearch:7700")
    with pytest.raises(ConfigurationError, match="PAPIQ_EMBEDDING_BASE_URL"):
        await run(settings)
    settings = Settings.model_construct(
        meilisearch_url="http://meilisearch:7700", embedding_base_url="http://ollama:11434/v1"
    )
    with pytest.raises(ConfigurationError, match="no model"):
        await run(settings)
    with pytest.raises(ConfigurationError, match="does not go with --fake"):
        await run(settings, fake=True, models=["x"])


async def test_a_query_for_an_unknown_case_stops_the_run(tmp_path: Path) -> None:
    shutil.copytree(SET, tmp_path / "set", ignore=shutil.ignore_patterns("reports"))
    write_queries(
        tmp_path / "set" / "search" / "queries.json",
        {"query": "Strom", "kind": "exact", "expected": ["99-nothing"]},
    )
    with pytest.raises(EvaluationSetError, match="unknown cases"):
        await run_search_evaluation(
            Settings.model_construct(), cases=tmp_path / "set", fake=True, output=tmp_path / "r.md"
        )


def test_the_command(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    report = tmp_path / "report.md"
    arguments = ["evaluate-search", "--fake", "--cases", str(SET), "--output", str(report)]
    assert main([*arguments, "--ratios", "0,1"]) == 0
    assert report.exists()
    assert "queries at ratio 1" in capsys.readouterr().out
    assert main([*arguments, "--ratios", "2"]) == 1
    assert main([*arguments, "--models", "x"]) == 1
    assert main(["evaluate-search", "--cases", str(tmp_path)]) == 1
    for other in ("check", "evaluate"):
        with pytest.raises(SystemExit):
            main([other, "--ratios", "0"])
        with pytest.raises(SystemExit):
            main([other, "--models", "x"])
        with pytest.raises(SystemExit):
            main([other, "--queries", "q.json"])
    with pytest.raises(SystemExit):
        main(["check", "--fake"])
