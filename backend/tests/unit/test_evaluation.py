"""The evaluation set with its fixed answers: the checks must catch every wrong answer."""

import json
import shutil
from pathlib import Path

import pytest

from papiq.adapters.inbound.evaluation import EvaluationSetError, load_set
from papiq.composition.__main__ import main
from papiq.composition.errors import ConfigurationError
from papiq.composition.evaluation import run_evaluation
from papiq.composition.settings import Settings
from papiq.core.domain.pipeline import Lane

SET = Path(__file__).parents[2] / "evaluation"


async def test_the_fixed_answers_give_the_expected_lanes(tmp_path: Path) -> None:
    report = tmp_path / "report.md"
    evaluation = await run_evaluation(
        Settings.model_construct(), cases=SET, fake=True, output=report
    )
    assert len(evaluation.results) >= 20
    for result in evaluation.results:
        assert result.case.fake is not None
        assert result.lane is result.case.fake.lane, (result.case.name, result.reasons)
        assert result.violations == (), result.case.name
    assert evaluation.false_green == 0
    assert evaluation.violations == 0

    text = report.read_text(encoding="utf-8")
    assert "- False green: **0**" in text
    assert "| green | " in text
    assert "## Instructions in the document text" in text


async def test_instructions_in_the_text_change_only_checked_fields(tmp_path: Path) -> None:
    evaluation = await run_evaluation(
        Settings.model_construct(), cases=SET, fake=True, output=tmp_path / "r.md"
    )
    injections = [result for result in evaluation.results if result.case.injection]
    assert len(injections) == 2
    for result in injections:
        assert result.lane is not Lane.GREEN
        assert result.violations == ()
    refused, obeyed = injections
    assert refused.lane is Lane.RED  # fields outside the schema
    assert "unexpected drawer" in refused.reasons[next(iter(refused.reasons))]
    assert obeyed.document_date is None  # not backed by the text
    assert "Betrag" not in obeyed.attributes  # invented


async def test_a_real_run_needs_a_model(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="PAPIQ_LLM_BASE_URL"):
        await run_evaluation(Settings.model_construct(), cases=SET, output=tmp_path / "r.md")


def test_the_set_is_checked(tmp_path: Path) -> None:
    with pytest.raises(EvaluationSetError, match=r"master_data\.json: missing"):
        load_set(tmp_path)
    shutil.copytree(SET, tmp_path / "set", ignore=shutil.ignore_patterns("reports"))
    case = tmp_path / "set" / "cases" / "01-stromrechnung.json"
    data = json.loads(case.read_text(encoding="utf-8"))
    data["expected"]["contact"] = "Nobody"
    case.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(EvaluationSetError, match="unknown contact 'Nobody'"):
        load_set(tmp_path / "set")


def test_the_command(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    report = tmp_path / "report.md"
    assert main(["evaluate", "--fake", "--cases", str(SET), "--output", str(report)]) == 0
    assert report.exists()
    assert "01-stromrechnung: green" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main(["check", "--fake"])
    assert main(["evaluate", "--cases", str(tmp_path)]) == 1
