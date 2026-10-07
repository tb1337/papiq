"""Embeddings: text to vector, for semantic search (indexing follows in M6).

First adapter: OpenAI-compatible API.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, kw_only=True)
class EmbeddingResult:
    vectors: list[tuple[float, ...]]  # one per text, in the order of the texts; equal length
    model: str  # the model as the endpoint reports it


class Embeddings(Protocol):
    @property
    def model(self) -> str:
        """The configured model name."""
        ...

    async def embed(self, texts: Sequence[str]) -> EmbeddingResult:
        """One vector per text. No texts, no vectors (and no request). EmbeddingsError if the
        endpoint cannot be reached, times out or answers with an error."""
        ...
