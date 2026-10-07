"""Stand-ins for the language model and the embeddings: deterministic, no network.

`FakeLanguageModel` answers with what its responder returns; without one, with the smallest
answer the request's schema allows (every nullable value null, every list empty), which is
"nothing recognised". `FakeEmbeddings` derives vectors from the SHA-256 of each text.
`BagOfWordsEmbeddings` derives them from the words, so that texts with words in common are
close: enough to test a search end to end, not to judge a model.
"""

import hashlib
import json
import math
import re
from collections.abc import Callable, Sequence

from papiq.core.domain.json_value import JsonObject, JsonValue
from papiq.core.ports.embeddings import EmbeddingResult
from papiq.core.ports.llm import StructuredAnswer, StructuredRequest

type Responder = Callable[[StructuredRequest], str | JsonObject]
"""Returns the answer to a request: text as is, or an object that is sent as JSON. May raise
LanguageModelError to simulate a failing endpoint."""


class FakeLanguageModel:
    def __init__(self, responder: Responder | None = None, *, model: str = "fake-llm 1") -> None:
        self._responder = responder or _nothing_recognised
        self._model = model
        self.requests: list[StructuredRequest] = []

    @property
    def model(self) -> str:
        return self._model

    @property
    def endpoint(self) -> str:
        return "memory"

    async def complete(self, request: StructuredRequest) -> StructuredAnswer:
        self.requests.append(request)
        answer = self._responder(request)
        content = answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)
        return StructuredAnswer(content=content, model=self._model)


def _nothing_recognised(request: StructuredRequest) -> str:
    return json.dumps(minimal_instance(request.schema))


def minimal_instance(schema: JsonValue) -> JsonValue:
    """The smallest value a (simple) JSON Schema accepts: null where allowed, empty lists and
    strings, the first enum value, objects with their required properties."""
    if not isinstance(schema, dict):
        return None
    options = schema.get("anyOf")
    if isinstance(options, list) and options:
        values = [minimal_instance(option) for option in options]
        return None if None in values else values[0]
    kinds = schema.get("type")
    kinds = kinds if isinstance(kinds, list) else [kinds]
    if "null" in kinds:
        return None
    enum = schema.get("enum")
    if isinstance(enum, list) and enum:
        return enum[0]
    if "object" in kinds:
        properties = schema.get("properties")
        properties = properties if isinstance(properties, dict) else {}
        required = schema.get("required")
        names = required if isinstance(required, list) else []
        return {str(name): minimal_instance(properties.get(str(name))) for name in names}
    if "array" in kinds:
        return []
    if "boolean" in kinds:
        return False
    if "integer" in kinds or "number" in kinds:
        return 0
    return ""


class FakeEmbeddings:
    DIMENSIONS = 16

    def __init__(self, *, model: str = "fake-embeddings 1") -> None:
        self._model = model
        self.calls: list[list[str]] = []

    @property
    def model(self) -> str:
        return self._model

    async def embed(self, texts: Sequence[str]) -> EmbeddingResult:
        if texts:
            self.calls.append(list(texts))
        return EmbeddingResult(vectors=[_vector(text) for text in texts], model=self._model)


class BagOfWordsEmbeddings:
    """Each word, and each three letters in a word of four or more, adds to a few of
    `dimensions` slots chosen by its hash (the hashing trick); the vector has length 1."""

    def __init__(self, *, dimensions: int = 256, model: str = "fake-bag-of-words 1") -> None:
        if dimensions < 2:
            raise ValueError("dimensions must be at least 2")
        self._dimensions = dimensions
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    async def embed(self, texts: Sequence[str]) -> EmbeddingResult:
        return EmbeddingResult(vectors=[self._vector(text) for text in texts], model=self._model)

    def _vector(self, text: str) -> tuple[float, ...]:
        slots = [0.0] * self._dimensions
        for word in _WORD.findall(text.casefold()):
            self._add(slots, word, 1.0)
            if len(word) >= 4:
                for start in range(len(word) - 2):
                    self._add(slots, word[start : start + 3], 0.3)
        norm = math.sqrt(sum(value * value for value in slots))
        if norm == 0:  # a text without words
            slots[0] = 1.0
            return tuple(slots)
        return tuple(value / norm for value in slots)

    def _add(self, slots: list[float], token: str, weight: float) -> None:
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        for part in (digest[:4], digest[4:]):
            number = int.from_bytes(part, "big")
            slots[number % self._dimensions] += weight if number & (1 << 31) else -weight


_WORD = re.compile(r"\w+")


def _vector(text: str) -> tuple[float, ...]:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return tuple(byte / 255 * 2 - 1 for byte in digest[: FakeEmbeddings.DIMENSIONS])
