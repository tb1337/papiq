"""Stand-ins for the language model and the embeddings: deterministic, no network.

`FakeLanguageModel` answers with what its responder returns; without one, with the smallest
answer the request's schema allows (every nullable value null, every list empty), which is
"nothing recognised". `FakeEmbeddings` derives vectors from the SHA-256 of each text.
"""

import hashlib
import json
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


def _vector(text: str) -> tuple[float, ...]:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return tuple(byte / 255 * 2 - 1 for byte in digest[: FakeEmbeddings.DIMENSIONS])
