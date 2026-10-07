"""`python -m papiq.composition evaluate`: run the evaluation set against the configured
language model (or the set's fixed answers) and write a Markdown report."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from papiq.adapters.inbound.evaluation import (
    Case,
    CaseResult,
    Environment,
    RunInfo,
    evaluate,
    load_set,
    render,
    summarise,
)
from papiq.adapters.outbound.memory import FakeLanguageModel
from papiq.composition.container import build_memory_container, openai_language_model, policy_of
from papiq.composition.errors import ConfigurationError
from papiq.composition.settings import Settings
from papiq.core.ports import LanguageModel
from papiq.core.services.classification.prompts import CLASSIFY_PROMPT, EXTRACT_PROMPT

FAKE_MODEL = "fixed answers"


@dataclass(frozen=True)
class Evaluation:
    report: Path
    results: list[CaseResult]
    false_green: int
    violations: int


async def run_evaluation(
    settings: Settings,
    *,
    cases: Path,
    fake: bool = False,
    output: Path | None = None,
    progress: bool = False,
) -> Evaluation:
    """Run the set in `cases`; write the report to `output` (default:
    `<cases>/reports/<date>-<model>.md`). Without `fake`, PAPIQ_LLM_BASE_URL is required."""
    evaluation = load_set(cases)
    model: LanguageModel | None = None
    if not fake:
        if settings.llm_base_url is None:
            raise ConfigurationError(
                "PAPIQ_LLM_BASE_URL is not set; configure a model or use --fake"
            )
        model = openai_language_model(settings)

    def model_for(case: Case) -> LanguageModel | None:
        if not fake:
            return model
        if case.fake is None:
            raise ConfigurationError(f"case {case.name} has no fixed answer for --fake")
        return FakeLanguageModel(case.fake.respond, model=FAKE_MODEL)

    def report_progress(result: CaseResult) -> None:
        lane = "none" if result.lane is None else result.lane.value
        print(f"{result.case.name}: {lane} ({result.seconds:.1f} s)", flush=True)

    container = build_memory_container()
    environment = Environment(container.unit_of_work, container.object_store, container.clock)
    started = datetime.now(UTC)
    try:
        results = await evaluate(
            evaluation,
            environment,
            model_for,
            policy_of(settings),
            progress=report_progress if progress else None,
        )
    finally:
        await container.aclose()
    info = RunInfo(
        model=FAKE_MODEL if model is None else model.model,
        endpoint="memory" if model is None else model.endpoint,
        started=started,
        prompts=f"{CLASSIFY_PROMPT}, {EXTRACT_PROMPT}",
        settings={
            "PAPIQ_LLM_INPUT_BUDGET": settings.llm_input_budget,
            "PAPIQ_LLM_MAX_TAGS": settings.llm_max_tags,
            "PAPIQ_LLM_TEMPERATURE": settings.llm_temperature,
            "PAPIQ_LLM_RESPONSE_FORMAT": settings.llm_response_format,
            "PAPIQ_CONFIDENCE_THRESHOLD": settings.confidence_threshold,
            "PAPIQ_CONTACT_SUGGEST_THRESHOLD": settings.contact_suggest_threshold,
        },
        fake=fake,
    )
    path = output or cases / "reports" / f"{started:%Y-%m-%d}-{_slug(info.model)}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(results, info), encoding="utf-8")
    summary = summarise(results, fake=fake)
    return Evaluation(path, results, len(summary.false_green), len(summary.violations))


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9.]+", "-", name.casefold()).strip("-") or "model"
