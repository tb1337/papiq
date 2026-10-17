"""A classifier for tests and the memory container: a class is the words its examples share, a
text belongs to it by how many of them it has. The model is JSON."""

import json
import re
from collections import Counter, defaultdict
from collections.abc import Hashable, Iterable, Sequence
from uuid import UUID

from papiq.core.domain.errors import ClassifierError
from papiq.core.domain.ids import ContactId, DocumentTypeId, TagId
from papiq.core.domain.json_value import JsonObject
from papiq.core.ports.classifier import Prediction, TrainedModel, TrainingExample

_WORD = re.compile(r"[^\W\d_]{3,}")
_FORMAT = "fake-words/1"


class FakeClassifier:
    name = "fake classifier"

    async def train(self, examples: Sequence[TrainingExample]) -> TrainedModel:
        if not any(_words(example.text) for example in examples):
            raise ClassifierError("no example has any text")
        contacts = _classes((example.contact, example.text) for example in examples)
        types = _classes((example.document_type, example.text) for example in examples)
        topics = _classes((example.topic, example.text) for example in examples)
        tags = {str(tag): sorted(words) for tag, words in _tag_words(examples).items()}
        data = {
            "format": _FORMAT,
            "contact": contacts,
            "document_type": types,
            "topic": topics,
            "tags": tags,
        }
        classes: JsonObject = {
            "contact": len(contacts),
            "document_type": len(types),
            "topic": len(topics),
            "tags": len(tags),
        }
        summary: JsonObject = {"classes": classes}
        return TrainedModel(json.dumps(data).encode(), summary)

    async def predict(self, model: bytes, text: str) -> Prediction:
        try:
            data = json.loads(model)
            if data.get("format") != _FORMAT:
                raise ClassifierError("the model is of another kind")
            words = _words(text)
            contacts = _ranked(data["contact"], words)
            types = _ranked(data["document_type"], words)
            topics = _ranked(data.get("topic", {}), words)
            tags = {TagId(UUID(tag)): _share(set(own), words) for tag, own in data["tags"].items()}
        except (ValueError, KeyError, TypeError, AttributeError):
            raise ClassifierError("the model cannot be read") from None
        return Prediction(
            contacts=[(ContactId(UUID(key)), share) for key, share in contacts],
            document_types=[(DocumentTypeId(UUID(key)), share) for key, share in types],
            tags=tags,
            topics=[(TagId(UUID(key)), share) for key, share in topics],
        )


def _words(text: str) -> set[str]:
    return {word.casefold() for word in _WORD.findall(text)}


def _classes(labelled: Iterable[tuple[Hashable | None, str]]) -> dict[str, list[str]]:
    """The words the examples of a class have in common, for classes of two or more examples;
    nothing unless there are two such classes."""
    texts: dict[str, list[set[str]]] = defaultdict(list)
    for label, text in labelled:
        if label is not None:
            texts[str(label)].append(_words(text))
    classes = {
        label: sorted(set.intersection(*sets)) for label, sets in texts.items() if len(sets) >= 2
    }
    return classes if len(classes) >= 2 else {}


def _tag_words(examples: Sequence[TrainingExample]) -> dict[TagId, set[str]]:
    counts = Counter(tag for example in examples for tag in example.tags)
    learned = {}
    for tag, count in counts.items():
        if count >= 2 and len(examples) - count >= 2:
            sets = [_words(example.text) for example in examples if tag in example.tags]
            learned[tag] = set.intersection(*sets)
    return learned


def _share(own: set[str], words: set[str]) -> float:
    return len(own & words) / len(own) if own else 0.0


def _ranked(classes: dict[str, list[str]], words: set[str]) -> list[tuple[str, float]]:
    shares = {label: _share(set(own), words) for label, own in classes.items()}
    total = sum(shares.values())
    if not shares:
        return []
    probabilities = {
        label: (share / total if total else 1 / len(shares)) for label, share in shares.items()
    }
    return sorted(probabilities.items(), key=lambda item: (-item[1], item[0]))
