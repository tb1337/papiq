"""Contracts of the language model and the embeddings."""

import json

import pytest

from papiq.core.domain.errors import EmbeddingsError, LanguageModelError
from papiq.core.domain.json_value import JsonObject
from papiq.core.ports import Embeddings, LanguageModel, StructuredRequest

SCHEMA: JsonObject = {
    "type": "object",
    "properties": {"answer": {"type": ["string", "null"]}},
    "required": ["answer"],
    "additionalProperties": False,
}
REQUEST = StructuredRequest(
    system="Answer in JSON.", user="Say ok.", schema=SCHEMA, schema_name="contract"
)


class LanguageModelContract:
    """Needs the fixtures `language_model`, whose endpoint answers `{"answer": "ok"}`, and
    `failing_language_model`, whose endpoint fails."""

    async def test_answers_with_text_and_the_model(self, language_model: LanguageModel) -> None:
        answer = await language_model.complete(REQUEST)
        assert json.loads(answer.content) == {"answer": "ok"}
        assert answer.model

    def test_names_model_and_endpoint(self, language_model: LanguageModel) -> None:
        assert language_model.model
        assert language_model.endpoint
        assert "@" not in language_model.endpoint
        assert "/" not in language_model.endpoint

    async def test_a_failing_endpoint_raises(self, failing_language_model: LanguageModel) -> None:
        with pytest.raises(LanguageModelError):
            await failing_language_model.complete(REQUEST)


class EmbeddingsContract:
    """Needs the fixtures `embeddings` and `failing_embeddings`."""

    async def test_one_vector_per_text(self, embeddings: Embeddings) -> None:
        result = await embeddings.embed(["Rechnung", "Mietvertrag", "Rechnung"])
        assert len(result.vectors) == 3
        assert len({len(vector) for vector in result.vectors}) == 1
        assert result.vectors[0]
        assert all(isinstance(value, float) for value in result.vectors[0])
        assert result.vectors[0] == result.vectors[2]
        assert result.vectors[0] != result.vectors[1]
        assert result.model

    async def test_no_texts_no_vectors(self, failing_embeddings: Embeddings) -> None:
        result = await failing_embeddings.embed([])
        assert result.vectors == []

    async def test_a_failing_endpoint_raises(self, failing_embeddings: Embeddings) -> None:
        with pytest.raises(EmbeddingsError):
            await failing_embeddings.embed(["text"])
