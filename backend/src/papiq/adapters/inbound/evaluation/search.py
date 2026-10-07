"""Search quality: which embedding model finds the right documents of the evaluation set.

The cases of the set become documents (the classification runs with their fixed answers, as in
`evaluate --fake`), each backend indexes them with its model through the real `IndexingService`,
and the queries of `queries.json` run through the real `SearchService` at each semantic ratio.
A query counts as found at rank r if the first of its expected cases is the r-th result.
"""

import json
import math
import time
from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from papiq.adapters.inbound.evaluation.cases import Case, EvaluationSet, EvaluationSetError
from papiq.adapters.inbound.evaluation.runner import Environment, evaluate
from papiq.core.domain.ids import DocumentId, UserId
from papiq.core.ports import EmbeddingResult, Embeddings, LanguageModel, SearchIndex
from papiq.core.services.classification.steps import ClassificationPolicy
from papiq.core.services.indexing import IndexingPolicy, IndexingService
from papiq.core.services.search import SearchPolicy, SearchService

CUTOFF = 10  # results looked at per query
KINDS_AT = 3  # the rank that counts as found when comparing kinds of queries


@dataclass(frozen=True)
class SearchCase:
    query: str
    kind: str
    expected: frozenset[str]  # case names; any of them answers the query


@dataclass(frozen=True)
class QuerySet:
    description: str
    cases: tuple[SearchCase, ...]

    @property
    def kinds(self) -> tuple[str, ...]:
        """In order of first appearance."""
        return tuple(dict.fromkeys(case.kind for case in self.cases))


def load_queries(path: Path, known: frozenset[str]) -> QuerySet:
    """Read `queries.json`; `known`: the names of the cases of the evaluation set."""
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise EvaluationSetError(f"cannot read {path}: {error.strerror or error}") from None
    except ValueError as error:
        raise EvaluationSetError(f"{path} is not valid JSON: {error}") from None
    items = data.get("queries") if isinstance(data, dict) else None
    if not isinstance(items, list) or not items:
        raise EvaluationSetError(f"{path} has no queries")
    cases = []
    for number, item in enumerate(items, start=1):
        where = f"{path}, query {number}"
        if not isinstance(item, dict):
            raise EvaluationSetError(f"{where} is not an object")
        query, kind, expected = item.get("query"), item.get("kind"), item.get("expected")
        if not isinstance(query, str) or not query.strip():
            raise EvaluationSetError(f"{where} has no query text")
        if not isinstance(kind, str) or not kind:
            raise EvaluationSetError(f"{where} has no kind")
        if not isinstance(expected, list) or not expected:
            raise EvaluationSetError(f"{where} expects no case")
        unknown = sorted(str(name) for name in expected if name not in known)
        if unknown:
            raise EvaluationSetError(f"{where} expects unknown cases: {', '.join(unknown)}")
        cases.append(SearchCase(query, kind, frozenset(expected)))
    description = data.get("description")
    return QuerySet(description if isinstance(description, str) else "", tuple(cases))


class TimedEmbeddings:
    """Embeddings that count the texts and the time spent in the wrapped ones."""

    def __init__(self, inner: Embeddings) -> None:
        self._inner = inner
        self.seconds = 0.0
        self.texts = 0
        self.calls = 0

    @property
    def model(self) -> str:
        return self._inner.model

    async def embed(self, texts: Sequence[str]) -> EmbeddingResult:
        started = time.perf_counter()
        try:
            return await self._inner.embed(texts)
        finally:
            self.seconds += time.perf_counter() - started
            self.texts += len(texts)
            self.calls += 1


@dataclass(frozen=True)
class Backend:
    """One embedding model and where its index lives. `open_index(dimensions)` hands out a
    fresh, empty index for the length of the vectors (None without embeddings) and removes it on
    leaving."""

    name: str
    embeddings: Embeddings | None
    open_index: Callable[[int | None], AbstractAsyncContextManager[SearchIndex]]


@dataclass(frozen=True)
class QueryOutcome:
    case: SearchCase
    rank: int | None  # of the first expected case among the first CUTOFF results
    got: tuple[str, ...]  # case names, best first
    seconds: float
    semantic: bool  # whether the meaning took part


@dataclass
class RatioResult:
    ratio: float
    outcomes: list[QueryOutcome] = field(default_factory=list)

    def found(self, rank: int, kind: str | None = None) -> int:
        return sum(
            1 for outcome in self._of(kind) if outcome.rank is not None and outcome.rank <= rank
        )

    def count(self, kind: str | None = None) -> int:
        return len(self._of(kind))

    def rate(self, rank: int, kind: str | None = None) -> float:
        count = self.count(kind)
        return self.found(rank, kind) / count if count else 0.0

    @property
    def mrr(self) -> float:
        """Mean reciprocal rank; a query not found within the cutoff counts 0."""
        rows = self._of(None)
        if not rows:
            return 0.0
        return sum(1 / o.rank for o in rows if o.rank is not None) / len(rows)

    @property
    def mean_ms(self) -> float:
        rows = self._of(None)
        return 1000 * sum(o.seconds for o in rows) / len(rows) if rows else 0.0

    @property
    def p95_ms(self) -> float:
        times = sorted(o.seconds for o in self._of(None))
        return 1000 * times[math.ceil(0.95 * len(times)) - 1] if times else 0.0

    @property
    def without_meaning(self) -> int:
        """Queries of a ratio above 0 for which the meaning did not take part."""
        return sum(1 for o in self._of(None) if self.ratio > 0 and not o.semantic)

    def _of(self, kind: str | None) -> list[QueryOutcome]:
        return [o for o in self.outcomes if kind is None or o.case.kind == kind]


@dataclass
class ModelResult:
    name: str
    dimensions: int | None
    index_seconds: float  # the whole rebuild
    embed_seconds: float  # of it, the time spent in the embedding endpoint
    sections: int  # texts embedded for the documents
    embed_calls: int
    ratios: list[RatioResult]


@dataclass(frozen=True)
class SearchRun:
    documents: int
    sections_per_document: float
    models: list[ModelResult]


@dataclass(frozen=True)
class SearchRunInfo:
    endpoint: str
    index: str  # what the documents were indexed in
    started: datetime
    settings: dict[str, object]
    fake: bool
    default_ratio: float


async def evaluate_search(
    evaluation: EvaluationSet,
    queries: QuerySet,
    environment: Environment,
    model_for: Callable[[Case], LanguageModel | None],
    policy: ClassificationPolicy,
    backends: Sequence[Backend],
    ratios: Sequence[float],
    *,
    indexing: IndexingPolicy | None = None,
    searching: SearchPolicy | None = None,
    progress: Callable[[str], None] | None = None,
) -> SearchRun:
    """Seed the documents of the set, then index them and run the queries for each backend."""
    say = progress or (lambda message: None)
    await evaluate(evaluation, environment, model_for, policy)
    names, owner = await _anonymise(environment)
    say(f"{len(names)} documents ready")
    models = []
    for backend in backends:
        models.append(
            await _measure(
                backend,
                queries,
                environment,
                names,
                owner,
                ratios,
                indexing or IndexingPolicy(),
                searching or SearchPolicy(embed_timeout=timedelta(minutes=2)),
                say,
            )
        )
    sections = [m.sections / len(names) for m in models if m.sections]
    return SearchRun(len(names), sections[0] if sections else 0.0, models)


async def _anonymise(environment: Environment) -> tuple[dict[DocumentId, str], UserId]:
    """The case name of each document; the title and file name then carry a number only, or the
    search would find a document by the name of its case."""
    names: dict[DocumentId, str] = {}
    owner: UserId | None = None
    async with environment.uow() as uow:
        documents = sorted(await uow.documents.list_all(), key=lambda d: d.original_filename)
        for number, document in enumerate(documents, start=1):
            names[document.id] = document.original_filename.removesuffix(".pdf")
            owner = document.owner_id
            document.original_filename = f"scan_{number:04d}.pdf"
            document.title = f"scan_{number:04d}"
            await uow.documents.update(document)
        await uow.commit()
    if owner is None:
        raise EvaluationSetError("the evaluation set has no cases")
    return names, owner


async def _measure(
    backend: Backend,
    queries: QuerySet,
    environment: Environment,
    names: dict[DocumentId, str],
    owner: UserId,
    ratios: Sequence[float],
    indexing: IndexingPolicy,
    searching: SearchPolicy,
    say: Callable[[str], None],
) -> ModelResult:
    timed = None if backend.embeddings is None else TimedEmbeddings(backend.embeddings)
    dimensions = None
    if timed is not None:
        probe = await timed.embed(["dimensions"])
        dimensions = len(probe.vectors[0])
        timed.seconds, timed.texts, timed.calls = 0.0, 0, 0
    async with backend.open_index(dimensions) as index:
        say(f"{backend.name}: indexing {len(names)} documents")
        started = time.perf_counter()
        await IndexingService(
            environment.uow, environment.clock, environment.store, index, timed, indexing
        ).rebuild()
        index_seconds = time.perf_counter() - started
        embed_seconds, sections, calls = (
            (0.0, 0, 0) if timed is None else (timed.seconds, timed.texts, timed.calls)
        )
        search = SearchService(environment.uow, index, timed, searching)
        results = []
        for ratio in ratios if timed is not None else [0.0]:
            say(f"{backend.name}: {len(queries.cases)} queries at ratio {ratio:g}")
            result = RatioResult(ratio)
            for case in queries.cases:
                result.outcomes.append(await _ask(search, owner, case, ratio, names))
            results.append(result)
    return ModelResult(
        name=backend.name,
        dimensions=dimensions,
        index_seconds=index_seconds,
        embed_seconds=embed_seconds,
        sections=sections,
        embed_calls=calls,
        ratios=results,
    )


async def _ask(
    search: SearchService,
    owner: UserId,
    case: SearchCase,
    ratio: float,
    names: dict[DocumentId, str],
) -> QueryOutcome:
    started = time.perf_counter()
    page = await search.search(owner, case.query, limit=CUTOFF, semantic_ratio=ratio)
    seconds = time.perf_counter() - started
    got = tuple(names[item.document.id] for item in page.items)
    rank = next((position for position, name in enumerate(got, 1) if name in case.expected), None)
    return QueryOutcome(case, rank, got, seconds, page.semantic)
