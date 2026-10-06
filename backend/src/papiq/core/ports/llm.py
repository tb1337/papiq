from typing import Protocol


class LanguageModel(Protocol):
    """Structured classification and extraction with a fixed output schema.

    First adapter: OpenAI-compatible API (Ollama locally, or a cloud provider).
    """
