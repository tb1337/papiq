from typing import Protocol


class Embeddings(Protocol):
    """Text to vector, for semantic search. First adapter: OpenAI-compatible API."""
