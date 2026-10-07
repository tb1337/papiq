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

__all__ = [
    "Case",
    "CaseResult",
    "Environment",
    "EvaluationSet",
    "EvaluationSetError",
    "FakeAnswer",
    "RunInfo",
    "Summary",
    "evaluate",
    "load_set",
    "render",
    "summarise",
]
