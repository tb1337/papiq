"""The scikit-learn classifier passes the contract; its own prediction (numpy on the stored
numbers) is scikit-learn's, and a model is plain arrays."""

import io

import numpy as np
import pytest

from papiq.adapters.outbound.sklearn import SklearnClassifier
from papiq.adapters.outbound.sklearn.classifier import FORMAT, _Model, analyze, fit
from papiq.core.domain.errors import ClassifierError
from papiq.core.ports import TrainingExample
from tests.contracts.classifier import INSURER, INVOICE, ClassifierContract, corpus


@pytest.fixture
def classifier() -> SklearnClassifier:
    return SklearnClassifier()


class TestSklearnClassifier(ClassifierContract):
    pass


def test_words_and_pairs() -> None:
    assert analyze("Rechnung R-2026 vom 31.03.2026: 84,20 €") == [
        "rechnung",
        "r-2026",
        "vom",
        "rechnung r-2026",
        "r-2026 vom",
    ]


@pytest.mark.parametrize(
    "text",
    [
        "Stadtwerke Musterstadt Zählerstand",
        "Beispielbank Saldo",
        "nichts davon",
        "",
    ],
)
def test_the_prediction_is_scikit_learns(text: str) -> None:
    data, _, fitted = fit(corpus())
    prediction = _Model.load(data).predict(text)
    vector = fitted.vectorizer.transform([text])
    assert fitted.contact is not None and fitted.document_type is not None
    expected = dict(
        zip(fitted.contact.classes_, fitted.contact.predict_proba(vector)[0], strict=True)
    )
    assert {str(key): value for key, value in prediction.contacts} == pytest.approx(expected)
    kinds = dict(
        zip(
            fitted.document_type.classes_,
            fitted.document_type.predict_proba(vector)[0],
            strict=True,
        )
    )
    assert {str(key): value for key, value in prediction.document_types} == pytest.approx(kinds)
    for tag, model in fitted.tags.items():
        assert prediction.tags[tag] == pytest.approx(model.predict_proba(vector)[0][1], abs=1e-5)
    assert fitted.topic is not None
    topics = dict(zip(fitted.topic.classes_, fitted.topic.predict_proba(vector)[0], strict=True))
    assert {str(key): value for key, value in prediction.topics} == pytest.approx(topics)


def test_a_model_is_plain_arrays() -> None:
    data, summary, _ = fit(corpus())
    with np.load(io.BytesIO(data), allow_pickle=False) as archive:
        assert str(archive["format"]) == FORMAT
        # Two classes: one row, for the second one (as scikit-learn keeps it).
        assert archive["contact_coef"].shape == (1, len(archive["terms"]))
    assert summary["classes"] == {"contact": 2, "document_type": 2, "topic": 2, "tags": 2}


def test_a_model_from_before_topics_knows_none() -> None:
    data, _, _ = fit(corpus())
    with np.load(io.BytesIO(data), allow_pickle=False) as archive:
        arrays = {name: archive[name] for name in archive.files if not name.startswith("topic_")}
    buffer = io.BytesIO()
    np.savez_compressed(buffer, **arrays)
    prediction = _Model.load(buffer.getvalue()).predict("Stadtwerke Musterstadt Zählerstand")
    assert prediction.topics == [] and prediction.contacts


def test_a_model_of_another_format() -> None:
    buffer = io.BytesIO()
    np.savez_compressed(buffer, format=np.array("other/9"))
    with pytest.raises(ClassifierError, match="another format"):
        _Model.load(buffer.getvalue())


def test_with_more_classes_too() -> None:
    insurer = [
        TrainingExample(f"Nord Versicherung Beitragsrechnung Police {n}", INSURER, INVOICE)
        for n in range(3)
    ]
    data, _, fitted = fit([*corpus(), *insurer])
    text = "Nord Versicherung Beitrag"
    prediction = _Model.load(data).predict(text)
    assert fitted.contact is not None and len(fitted.contact.classes_) == 3
    probabilities = fitted.contact.predict_proba(fitted.vectorizer.transform([text]))[0]
    expected = dict(zip(fitted.contact.classes_, probabilities, strict=True))
    assert {str(key): value for key, value in prediction.contacts} == pytest.approx(expected)
    assert prediction.contacts[0][0] == INSURER
