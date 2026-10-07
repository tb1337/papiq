"""The evaluation set: synthetic documents with expected metadata, run through the
classification steps to compare models (`python -m papiq.composition evaluate`)."""

from papiq.adapters.inbound.evaluation.cases import (
    Case,
    EvaluationSet,
    EvaluationSetError,
    FakeAnswer,
    load_set,
)
from papiq.adapters.inbound.evaluation.report import RunInfo, Summary, render, summarise
from papiq.adapters.inbound.evaluation.runner import CaseResult, Environment, evaluate
from papiq.adapters.inbound.evaluation.search import (
    Backend,
    ModelResult,
    QuerySet,
    SearchRun,
    SearchRunInfo,
    evaluate_search,
    load_queries,
)
from papiq.adapters.inbound.evaluation.search_report import render_search

__all__ = [
    "Backend",
    "Case",
    "CaseResult",
    "Environment",
    "EvaluationSet",
    "EvaluationSetError",
    "FakeAnswer",
    "ModelResult",
    "QuerySet",
    "RunInfo",
    "SearchRun",
    "SearchRunInfo",
    "Summary",
    "evaluate",
    "evaluate_search",
    "load_queries",
    "load_set",
    "render",
    "render_search",
    "summarise",
]
