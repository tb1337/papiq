"""`python -m papiq.composition evaluate-search`: index the evaluation set with one or more
embedding models and measure which queries find which documents; write a Markdown report."""

import re
import secrets
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from papiq.adapters.inbound.evaluation import (
    Backend,
    Case,
    Environment,
    ModelResult,
    SearchRun,
    SearchRunInfo,
    evaluate_search,
    load_queries,
    load_set,
    render_search,
)
from papiq.adapters.outbound.meilisearch import MeilisearchIndex
from papiq.adapters.outbound.memory import (
    BagOfWordsEmbeddings,
    FakeLanguageModel,
    MemorySearchIndex,
)
from papiq.adapters.outbound.openai_compat import endpoint_of
from papiq.composition.container import (
    build_memory_container,
    indexing_policy_of,
    openai_embeddings,
    policy_of,
    search_policy_of,
)
from papiq.composition.errors import ConfigurationError
from papiq.composition.settings import Settings
from papiq.core.ports import LanguageModel, SearchIndex

DEFAULT_RATIOS = (0.0, 0.5, 1.0)
QUERY_TIMEOUT = timedelta(minutes=2)  # a model may be loaded into memory on the first query
FAKE_MODEL = "fixed answers"


@dataclass(frozen=True)
class SearchEvaluation:
    report: Path
    run: SearchRun

    @property
    def models(self) -> list[ModelResult]:
        return self.run.models


def parse_ratios(text: str) -> tuple[float, ...]:
    try:
        ratios = tuple(float(part) for part in text.split(",") if part.strip())
    except ValueError:
        raise ConfigurationError(f"--ratios is not a list of numbers: {text!r}") from None
    if not ratios or any(not 0 <= ratio <= 1 for ratio in ratios):
        raise ConfigurationError("--ratios needs numbers between 0 and 1, like 0,0.5,1")
    return tuple(dict.fromkeys(ratios))


async def run_search_evaluation(
    settings: Settings,
    *,
    cases: Path,
    queries: Path | None = None,
    fake: bool = False,
    models: Sequence[str] = (),
    ratios: Sequence[float] = DEFAULT_RATIOS,
    output: Path | None = None,
    progress: bool = False,
) -> SearchEvaluation:
    """Run the queries of `queries` (default: `<cases>/search/queries.json`) on the set in
    `cases` for each model; write the report to `output` (default:
    `<cases>/search/reports/<date>-<models>.md`).

    Without `fake`, PAPIQ_MEILISEARCH_URL and PAPIQ_EMBEDDING_BASE_URL are required; every model
    gets a temporary Meilisearch index, removed afterwards. With `fake`, bag-of-words vectors and
    the in-memory index take their place.
    """
    evaluation = load_set(cases)
    query_set = load_queries(
        queries or cases / "search" / "queries.json",
        frozenset(case.name for case in evaluation.cases),
    )
    if fake and models:
        raise ConfigurationError("--models does not go with --fake")
    backends, endpoint, index = _backends(settings, fake=fake, models=models)

    def model_for(case: Case) -> LanguageModel | None:
        # The documents are classified with the fixed answers: the metadata is not what is
        # measured here, and the same documents go to every model.
        if case.fake is None:
            raise ConfigurationError(f"case {case.name} has no fixed answer")
        return FakeLanguageModel(case.fake.respond, model=FAKE_MODEL)

    def say(message: str) -> None:
        print(message, flush=True)

    container = build_memory_container()
    environment = Environment(container.unit_of_work, container.object_store, container.clock)
    started = datetime.now(UTC)
    try:
        run = await evaluate_search(
            evaluation,
            query_set,
            environment,
            model_for,
            policy_of(settings),
            backends,
            ratios,
            # The length of the vectors is measured per model, not the configured one.
            indexing=replace(indexing_policy_of(settings), dimensions=None),
            searching=replace(
                search_policy_of(settings), embed_timeout=QUERY_TIMEOUT, dimensions=None
            ),
            progress=say if progress else None,
        )
    finally:
        await container.aclose()
    info = SearchRunInfo(
        endpoint=endpoint,
        index=index,
        started=started,
        settings={
            "PAPIQ_SEARCH_CHUNK_SIZE": settings.search_chunk_size,
            "PAPIQ_SEARCH_MAX_CHUNKS": settings.search_max_chunks,
            "PAPIQ_SEARCH_MAX_TEXT": settings.search_max_text,
            "PAPIQ_SEARCH_SEMANTIC_RATIO": settings.search_semantic_ratio,
            "PAPIQ_EMBEDDING_DOCUMENT_PREFIX": settings.embedding_document_prefix or "",
            "PAPIQ_EMBEDDING_QUERY_PREFIX": settings.embedding_query_prefix or "",
        },
        fake=fake,
        default_ratio=settings.search_semantic_ratio,
    )
    names = "-".join(_slug(model.name) for model in run.models)
    path = output or cases / "search" / "reports" / f"{started:%Y-%m-%d}-{names}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_search(run, query_set, info), encoding="utf-8")
    return SearchEvaluation(path, run)


def _backends(
    settings: Settings, *, fake: bool, models: Sequence[str]
) -> tuple[list[Backend], str, str]:
    """The backends, the endpoint and the kind of index for the report."""
    if fake:
        embeddings = BagOfWordsEmbeddings()

        @asynccontextmanager
        async def memory(dimensions: int | None) -> AsyncIterator[SearchIndex]:
            yield MemorySearchIndex(dimensions=dimensions)

        return [Backend(embeddings.model, embeddings, memory)], "memory", "in memory"
    if settings.meilisearch_url is None:
        raise ConfigurationError("PAPIQ_MEILISEARCH_URL is not set; use --fake or configure it")
    if settings.embedding_base_url is None:
        raise ConfigurationError("PAPIQ_EMBEDDING_BASE_URL is not set; use --fake or configure it")
    names = list(
        dict.fromkeys(models or ([settings.embedding_model] if settings.embedding_model else []))
    )
    if not names:
        raise ConfigurationError("no model: pass --models or set PAPIQ_EMBEDDING_MODEL")

    @asynccontextmanager
    async def meilisearch(dimensions: int | None) -> AsyncIterator[SearchIndex]:
        assert settings.meilisearch_url is not None
        key = settings.meilisearch_api_key
        index = MeilisearchIndex(
            url=str(settings.meilisearch_url),
            api_key=None if key is None else key.get_secret_value(),
            index=f"papiq-eval-{secrets.token_hex(4)}",
            dimensions=dimensions,
            timeout=settings.meilisearch_timeout.total_seconds(),
            task_timeout=settings.meilisearch_task_timeout.total_seconds(),
        )
        try:
            await index.check()
            yield index
        finally:
            try:
                await index.drop()
            finally:
                await index.aclose()

    backends = [
        Backend(
            name,
            openai_embeddings(settings.model_copy(update={"embedding_model": name})),
            meilisearch,
        )
        for name in names
    ]
    return (
        backends,
        endpoint_of(str(settings.embedding_base_url)),
        f"Meilisearch at {endpoint_of(str(settings.meilisearch_url))}, a temporary index per model",
    )


def _with_model(settings: Settings, model: str) -> Settings:
    return settings.model_copy(update={"embedding_model": model})


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9.]+", "-", name.casefold()).strip("-") or "model"
