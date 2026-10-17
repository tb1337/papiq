"""The evaluation set with its fixed answers, cold and trained: the checks must catch every
wrong answer."""

import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from papiq.adapters.inbound.evaluation import EvaluationSetError, Run, evaluate, load_set
from papiq.adapters.inbound.evaluation.cases import Case
from papiq.adapters.outbound.memory import FakeLanguageModel
from papiq.composition.__main__ import main
from papiq.composition.errors import ConfigurationError
from papiq.composition.evaluation import (
    FAKE_MODEL,
    build_environment,
    evaluation_container,
    run_evaluation,
)
from papiq.composition.settings import Settings
from papiq.core.domain.identifiers import IdentifierKind, IdentifierSource
from papiq.core.domain.json_value import JsonObject
from papiq.core.domain.pipeline import Lane
from papiq.core.ports.llm import StructuredRequest

SET = Path(__file__).parents[2] / "evaluation"


async def test_the_fixed_answers_give_the_expected_lanes(tmp_path: Path) -> None:
    report = tmp_path / "report.md"
    evaluation = await run_evaluation(
        Settings.model_construct(), cases=SET, fake=True, output=report
    )
    assert len(evaluation.results) >= 60
    assert {result.run for result in evaluation.results} == set(Run)
    for result in evaluation.results:
        assert result.case.fake is not None
        lane = result.case.fake.lane_in(result.run)
        assert result.lane is lane, (result.run, result.case.name, result.reasons)
        assert result.violations == (), result.case.name
    assert evaluation.false_green == 0
    assert evaluation.green_but_wrong == 0
    assert evaluation.violations == 0

    text = report.read_text(encoding="utf-8")
    assert "| False green | **0** | **0** |" in text
    assert "the trained one took over 57 documents of the training corpus first" in text
    assert "## Lanes (trained)" in text
    assert "## Instructions in the document text (cold)" in text


async def test_instructions_in_the_text_change_only_checked_fields(tmp_path: Path) -> None:
    evaluation = await run_evaluation(
        Settings.model_construct(), cases=SET, fake=True, output=tmp_path / "r.md"
    )
    injections = [result for result in evaluation.results if result.case.injection]
    assert len(injections) == 4
    injections = [result for result in injections if result.run is Run.COLD]
    for result in injections:
        assert result.lane is not Lane.GREEN
        assert result.violations == ()
    refused, obeyed = injections
    assert refused.lane is Lane.RED  # fields outside the schema
    assert "unexpected owner, share_with, space" in refused.reasons[next(iter(refused.reasons))]
    assert obeyed.document_date is None  # not backed by the text
    assert "Betrag" not in obeyed.fields  # invented


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


def test_the_set_takes_aliases_and_descriptions(tmp_path: Path) -> None:
    shutil.copytree(SET, tmp_path / "set", ignore=shutil.ignore_patterns("reports"))
    master = tmp_path / "set" / "master_data.json"
    data = json.loads(master.read_text(encoding="utf-8"))
    contact, kind = data["contacts"][0], data["document_types"][0]
    data["contact_aliases"] = {contact: ["Other name"]}
    data["type_descriptions"] = {kind: "What belongs to it"}
    master.write_text(json.dumps(data), encoding="utf-8")
    loaded = load_set(tmp_path / "set").master_data
    assert loaded.contact_aliases == {contact: ("Other name",)}
    assert loaded.type_descriptions == {kind: "What belongs to it"}
    data["contact_aliases"] = {"Nobody": ["x"]}
    master.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(EvaluationSetError, match="contact_aliases names unknown"):
        load_set(tmp_path / "set")


async def test_green_with_a_wrong_value_counts(tmp_path: Path) -> None:
    shutil.copytree(SET, tmp_path / "set", ignore=shutil.ignore_patterns("reports"))
    case = tmp_path / "set" / "cases" / "01-stromrechnung.json"
    data = json.loads(case.read_text(encoding="utf-8"))
    data["expected"]["document_date"] = "2026-01-01"
    case.write_text(json.dumps(data), encoding="utf-8")
    evaluation = await run_evaluation(
        Settings.model_construct(), cases=tmp_path / "set", fake=True, output=tmp_path / "r.md"
    )
    assert evaluation.green_but_wrong == 2  # cold and trained
    assert "01-stromrechnung (document_date)" in (tmp_path / "r.md").read_text(encoding="utf-8")
    # The person sets the expected date: the log tells they corrected the automatic one.
    (cold, trained) = [item for item in evaluation.results if item.case.name == case.stem]
    assert "document_date" in cold.corrected & cold.automatic
    assert "document_date" in trained.corrected & trained.automatic


def test_the_command(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    report = tmp_path / "report.md"
    assert main(["evaluate", "--fake", "--cases", str(SET), "--output", str(report)]) == 0
    assert report.exists()
    assert "cold: 01-stromrechnung: green" in capsys.readouterr().out
    cold = ["evaluate", "--fake", "--runs", "cold", "--cases", str(SET), "--output", str(report)]
    assert main(cold) == 0
    assert "trained:" not in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main(["evaluate", "--fake", "--runs", "warm"])
    with pytest.raises(SystemExit):
        main(["check", "--fake"])
    with pytest.raises(SystemExit):
        main(["check", "--cases", str(SET)])
    assert main(["evaluate", "--cases", str(tmp_path)]) == 1


async def test_the_trained_run_learns_from_the_corpus() -> None:
    """The corpus is taken over and trained on: identifiers that stand on one contact's letters
    only are learned, the household's IBAN (on letters of many) and the utility's (also on the
    bank statements) are not. The sender known only by its identifiers is then green."""
    evaluation = load_set(SET)
    container = evaluation_container(Run.TRAINED)
    environment = build_environment(
        container,
        Settings.model_construct(),
        lambda case: FakeLanguageModel(case.fake.respond, model=FAKE_MODEL) if case.fake else None,
        Run.TRAINED,
    )
    try:
        results = await evaluate(evaluation, environment)
        async with container.unit_of_work() as uow:
            contacts = {contact.name: contact for contact in await uow.contacts.list_all()}
    finally:
        await container.aclose()
    learned = {
        name: {
            (item.kind, item.value)
            for item in contact.identifiers
            if item.source is IdentifierSource.LEARNED
        }
        for name, contact in contacts.items()
    }
    assert (IdentifierKind.VAT_ID, "DE276543210") in learned["Versandhaus Bergmann GmbH"]
    every = {value for identifiers in learned.values() for _, value in identifiers}
    assert "DE89370400440532013000" not in every
    assert "DE12250501800004471100" not in every
    # Entered by hand, it stays so.
    insurer = contacts["Hanse Versicherung AG"]
    assert any(item.source is IdentifierSource.MANUAL for item in insurer.identifiers)
    # The health insurer's letters all carry its topic: the domain learned points to it.
    (domain,) = [
        item
        for item in contacts["Gesundkasse BKK"].identifiers
        if item.kind is IdentifierKind.EMAIL_DOMAIN
    ]
    assert domain.source is IdentifierSource.LEARNED and domain.topic_id is not None
    (logo,) = [result for result in results if result.case.name == "31-absender-nur-kennung"]
    assert logo.lane is Lane.GREEN
    (best, *_) = logo.checks["contact"].candidates
    assert any(source.identifiers for source in best.sources)


async def test_the_topic_is_set_by_an_identifier_and_a_signal_and_never_waits(
    tmp_path: Path,
) -> None:
    """The insurance group is employer and insurer: its personnel number and its policy number
    point to two topics. Each with the model sets the topic; both leave it empty with both as
    candidates; under a contact only suggested the topic is only suggested."""
    evaluation = await run_evaluation(
        Settings.model_construct(), cases=SET, fake=True, output=tmp_path / "r.md"
    )
    by_case = {
        (result.run, result.case.name): result
        for result in evaluation.results
        if result.case.name[:2] in {"39", "40", "41", "42"}
    }
    for run in Run:
        pay, policy, both, logo = (
            by_case[(run, name)]
            for name in (
                "39-lohnabrechnung-versicherung",
                "40-krankenversicherung-versicherungsschein",
                "41-kennungen-zweier-themen",
                "42-gelber-kontakt-thema",
            )
        )
        assert (pay.lane, pay.topic) == (Lane.GREEN, "Arbeit")
        assert (policy.lane, policy.topic) == (Lane.GREEN, "Krankenversicherung")
        check = both.checks["topic"]
        assert (both.lane, both.topic, check.value, check.suggestion) == (
            Lane.GREEN,
            None,
            None,
            None,
        )
        assert {"Arbeit", "Krankenversicherung"} <= {item.label for item in check.candidates}
        assert (logo.lane, logo.topic) == (Lane.YELLOW, None)
        assert logo.proposed["topic"] == "Krankenversicherung"


async def test_a_field_s_description_reaches_the_model() -> None:
    """The pay slip lists the base salary before the gross total; the description of the
    field asks for the gross total, and the prompt carries it."""
    evaluation = load_set(SET)
    (case,) = [item for item in evaluation.cases if item.name == "35-lohnabrechnung-gesamtbrutto"]
    asked: list[str] = []

    def model(item: Case) -> FakeLanguageModel | None:
        if item.fake is None:
            return None
        fake = item.fake

        def respond(request: StructuredRequest) -> JsonObject:
            asked.append(request.user)
            return fake.respond(request)

        return FakeLanguageModel(respond, model=FAKE_MODEL)

    container = evaluation_container(Run.COLD)
    environment = build_environment(container, Settings.model_construct(), model, Run.COLD)
    try:
        (result,) = await evaluate(replace(evaluation, cases=(case,)), environment)
    finally:
        await container.aclose()
    assert result.lane is Lane.GREEN, result.reasons
    assert any("Das Gesamtbrutto des Monats, nicht das Grundgehalt" in text for text in asked)
