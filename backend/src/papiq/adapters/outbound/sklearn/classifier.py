"""Classifier on scikit-learn: TF-IDF of words and word pairs, and a logistic regression for the
contact, one for the document type, one for the topic and one per tag, as Paperless-ngx learns
its matching.

scikit-learn only fits. The model is the fitted numbers in an npz archive (vocabulary, inverse
document frequencies, coefficients, classes), read with `allow_pickle=False`: a model cannot
run code, and it outlives an update of scikit-learn. Predicting is a few lines of numpy
(`_Model`), checked against scikit-learn's own in the tests. A model trained before topics has
no topic arrays: it knows no topic.
"""

import asyncio
import io
import itertools
import re
from collections import Counter, OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
from typing import Any
from uuid import UUID

import numpy as np
import sklearn
from numpy.typing import NDArray
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression

from papiq.core.domain.errors import ClassifierError
from papiq.core.domain.ids import ContactId, DocumentTypeId, TagId
from papiq.core.domain.json_value import JsonObject
from papiq.core.ports.classifier import Prediction, TrainedModel, TrainingExample

FORMAT = "papiq-tfidf-logreg/1"
MAX_TERMS = 20_000
MAX_CHARACTERS = 100_000  # of a text: the start and the end of a long one say what it is
# A word in more than half of the documents (the owner's name and address) tells nothing; one
# in a single document is a typo or a number. Small corpora keep every word.
MIN_DF, MAX_DF, SMALL = 2, 0.5, 20
C = 4.0  # inverse regularisation: TF-IDF features are many and sparse
_TOKEN = re.compile(r"(?:[^\W_]|(?<=[^\W_])[-.](?=[^\W_]))*[^\W\d_](?:[^\W_]|[-.](?=[^\W_]))*")
_CACHED = 2  # decoded models kept per process

type _Array = NDArray[Any]


def analyze(text: str) -> list[str]:
    """Words with at least one letter (`R-2026` counts, `31.03.2026` not), in lower case, and
    the pairs of neighbouring words."""
    if len(text) > MAX_CHARACTERS:
        half = MAX_CHARACTERS // 2
        text = text[:half] + " " + text[-half:]
    words = [word for word in _TOKEN.findall(text.casefold()) if len(word) >= 2]
    return words + [f"{left} {right}" for left, right in itertools.pairwise(words)]


class SklearnClassifier:
    def __init__(self) -> None:
        self._models: OrderedDict[str, _Model] = OrderedDict()

    @property
    def name(self) -> str:
        return f"scikit-learn {sklearn.__version__} {FORMAT}"

    async def train(self, examples: Sequence[TrainingExample]) -> TrainedModel:
        data, summary, _ = await asyncio.to_thread(fit, list(examples))
        return TrainedModel(data, summary)

    async def predict(self, model: bytes, text: str) -> Prediction:
        key = sha256(model).hexdigest()
        decoded = self._models.get(key)
        if decoded is None:
            decoded = await asyncio.to_thread(_Model.load, model)
            self._models[key] = decoded
            while len(self._models) > _CACHED:
                self._models.popitem(last=False)
        self._models.move_to_end(key)
        return await asyncio.to_thread(decoded.predict, text)


@dataclass(frozen=True)
class Fitted:
    """What scikit-learn fitted, for the tests' comparison."""

    vectorizer: TfidfVectorizer
    contact: LogisticRegression | None
    document_type: LogisticRegression | None
    tags: dict[TagId, LogisticRegression]
    topic: LogisticRegression | None = None


def fit(examples: list[TrainingExample]) -> tuple[bytes, JsonObject, Fitted]:
    """The model of the examples as an npz archive, a summary, and what was fitted."""
    texts = [example.text for example in examples]
    if not any(analyze(text) for text in texts):
        raise ClassifierError("no example has any text")
    small = len(texts) < SMALL
    vectorizer = TfidfVectorizer(
        analyzer=analyze,
        sublinear_tf=True,
        min_df=1 if small else MIN_DF,
        max_df=1.0 if small else MAX_DF,
        max_features=MAX_TERMS,
        dtype=np.float32,
    )
    try:
        matrix = vectorizer.fit_transform(texts)
    except ValueError as error:
        raise ClassifierError(f"the examples have too few words: {error}") from None
    contact = _single(matrix, [example.contact for example in examples])
    document_type = _single(matrix, [example.document_type for example in examples])
    topic = _single(matrix, [example.topic for example in examples])
    tags = _tags(matrix, examples)
    vocabulary = vectorizer.vocabulary_
    terms = sorted(vocabulary, key=vocabulary.__getitem__)
    arrays: dict[str, _Array] = {
        "format": np.array(FORMAT),
        "sklearn": np.array(sklearn.__version__),
        "terms": np.array(terms, dtype=str),
        "idf": vectorizer.idf_.astype(np.float32),
        **_target("contact", contact, len(terms)),
        **_target("document_type", document_type, len(terms)),
        **_target("topic", topic, len(terms)),
        **_tag_arrays(tags, len(terms)),
    }
    buffer = io.BytesIO()
    np.savez_compressed(buffer, **arrays)  # type: ignore[arg-type]
    classes: JsonObject = {
        "contact": 0 if contact is None else len(contact.classes_),
        "document_type": 0 if document_type is None else len(document_type.classes_),
        "topic": 0 if topic is None else len(topic.classes_),
        "tags": len(tags),
    }
    summary: JsonObject = {
        "format": FORMAT,
        "examples": len(examples),
        "terms": len(terms),
        "classes": classes,
    }
    return buffer.getvalue(), summary, Fitted(vectorizer, contact, document_type, tags, topic)


def _single(matrix: Any, labels: Sequence[UUID | None]) -> LogisticRegression | None:
    """A regression over the classes with at least two examples; None for fewer than two."""
    counts = Counter(label for label in labels if label is not None)
    kept = {label for label, count in counts.items() if count >= 2}
    if len(kept) < 2:
        return None
    rows = [index for index, label in enumerate(labels) if label in kept]
    model = LogisticRegression(C=C, class_weight="balanced", max_iter=1000)
    model.fit(matrix[rows], [str(labels[index]) for index in rows])
    return model


def _tags(matrix: Any, examples: list[TrainingExample]) -> dict[TagId, LogisticRegression]:
    """A regression per tag that at least two documents carry and two do not."""
    counts = Counter(tag for example in examples for tag in example.tags)
    fitted = {}
    for tag, count in sorted(counts.items(), key=lambda item: str(item[0])):
        if count >= 2 and len(examples) - count >= 2:
            model = LogisticRegression(C=C, class_weight="balanced", max_iter=1000)
            model.fit(matrix, [tag in example.tags for example in examples])
            fitted[tag] = model
    return fitted


def _target(name: str, model: LogisticRegression | None, terms: int) -> dict[str, _Array]:
    if model is None:
        return {
            f"{name}_classes": np.array([], dtype=str),
            f"{name}_coef": np.zeros((0, terms), dtype=np.float32),
            f"{name}_intercept": np.zeros(0, dtype=np.float32),
        }
    return {
        f"{name}_classes": np.array([str(item) for item in model.classes_], dtype=str),
        f"{name}_coef": model.coef_.astype(np.float32),
        f"{name}_intercept": model.intercept_.astype(np.float32),
    }


def _tag_arrays(tags: dict[TagId, LogisticRegression], terms: int) -> dict[str, _Array]:
    if not tags:
        coef, intercept = np.zeros((0, terms), dtype=np.float32), np.zeros(0, dtype=np.float32)
    else:
        coef = np.vstack([model.coef_[0] for model in tags.values()]).astype(np.float32)
        intercept = np.array([model.intercept_[0] for model in tags.values()], dtype=np.float32)
    return {
        "tag_classes": np.array([str(tag) for tag in tags], dtype=str),
        "tag_coef": coef,
        "tag_intercept": intercept,
    }


@dataclass(frozen=True)
class _Single:
    classes: list[str]
    coef: _Array
    intercept: _Array

    def probabilities(self, indices: _Array, weights: _Array) -> list[tuple[str, float]]:
        if not self.classes:
            return []
        scores = self.coef[:, indices] @ weights + self.intercept
        if len(self.classes) == 2 and len(scores) == 1:  # binary: one row for the second class
            second = float(_sigmoid(scores)[0])
            probabilities = [1 - second, second]
        else:
            exp = np.exp(scores - scores.max())
            probabilities = [float(value) for value in exp / exp.sum()]
        ranked = zip(self.classes, probabilities, strict=True)
        return sorted(ranked, key=lambda item: (-item[1], item[0]))


@dataclass(frozen=True)
class _Model:
    index: dict[str, int]
    idf: _Array
    contact: _Single
    document_type: _Single
    topic: _Single
    tag_classes: list[str]
    tag_coef: _Array
    tag_intercept: _Array

    @classmethod
    def load(cls, data: bytes) -> "_Model":
        try:
            with np.load(io.BytesIO(data), allow_pickle=False) as archive:
                if str(archive["format"]) != FORMAT:
                    raise ClassifierError(f"the model is in another format: {archive['format']}")
                terms = [str(term) for term in archive["terms"]]
                return cls(
                    index={term: number for number, term in enumerate(terms)},
                    idf=archive["idf"],
                    contact=_single_of(archive, "contact"),
                    document_type=_single_of(archive, "document_type"),
                    topic=_single_of(archive, "topic", optional=True),
                    tag_classes=[str(tag) for tag in archive["tag_classes"]],
                    tag_coef=archive["tag_coef"],
                    tag_intercept=archive["tag_intercept"],
                )
        except ClassifierError:
            raise
        except (ValueError, KeyError, OSError, EOFError) as error:
            raise ClassifierError(f"the model cannot be read: {error}") from None

    def predict(self, text: str) -> Prediction:
        indices, weights = self._vector(text)
        tags: dict[TagId, float] = {}
        if self.tag_classes:
            scores = self.tag_coef[:, indices] @ weights + self.tag_intercept
            tags = {
                TagId(UUID(tag)): float(value)
                for tag, value in zip(self.tag_classes, _sigmoid(scores), strict=True)
            }
        return Prediction(
            contacts=[
                (ContactId(UUID(label)), share)
                for label, share in self.contact.probabilities(indices, weights)
            ],
            document_types=[
                (DocumentTypeId(UUID(label)), share)
                for label, share in self.document_type.probabilities(indices, weights)
            ],
            tags=tags,
            topics=[
                (TagId(UUID(label)), share)
                for label, share in self.topic.probabilities(indices, weights)
            ],
        )

    def _vector(self, text: str) -> tuple[_Array, _Array]:
        """The text's TF-IDF weights (sublinear term frequency, length 1) by term index."""
        counts = Counter(self.index[term] for term in analyze(text) if term in self.index)
        indices = np.fromiter(counts.keys(), dtype=np.int64, count=len(counts))
        frequencies = np.fromiter(counts.values(), dtype=np.float32, count=len(counts))
        weights = (1 + np.log(frequencies)) * self.idf[indices]
        norm = float(np.linalg.norm(weights))
        return indices, (weights / norm if norm else weights)


def _single_of(archive: Any, name: str, *, optional: bool = False) -> _Single:
    """The regression of a target; an `optional` one a model may lack is empty then."""
    if optional and f"{name}_classes" not in archive.files:
        return _Single(classes=[], coef=np.zeros((0, 0)), intercept=np.zeros(0))
    return _Single(
        classes=[str(item) for item in archive[f"{name}_classes"]],
        coef=archive[f"{name}_coef"],
        intercept=archive[f"{name}_intercept"],
    )


def _sigmoid(values: _Array) -> _Array:
    result: _Array = 1 / (1 + np.exp(-values))
    return result
