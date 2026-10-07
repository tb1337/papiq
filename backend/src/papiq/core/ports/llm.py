"""Language model: one structured request, one answer.

First adapter: OpenAI-compatible API (Ollama locally, or a cloud provider). The answer is
returned as text; checking it against the schema is the core's job, because providers enforce
schemas with different reliability.
"""

from dataclasses import dataclass
from typing import Protocol

from papiq.core.domain.json_value import JsonObject


@dataclass(frozen=True, kw_only=True)
class StructuredRequest:
    """A system and a user message, and the JSON Schema the answer must follow."""

    system: str
    user: str
    schema: JsonObject
    schema_name: str  # letters, digits, `_` and `-`; some providers require a name


@dataclass(frozen=True, kw_only=True)
class StructuredAnswer:
    content: str  # the model's text; expected to be JSON, not checked
    model: str  # the model as the endpoint reports it
    fingerprint: str | None = None  # the endpoint's system fingerprint, if any
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class LanguageModel(Protocol):
    @property
    def model(self) -> str:
        """The configured model name."""
        ...

    @property
    def endpoint(self) -> str:
        """Where document content is sent (host and port, no credentials), for the log."""
        ...

    async def complete(self, request: StructuredRequest) -> StructuredAnswer:
        """Ask the model. Deterministic as far as the endpoint allows (configured temperature,
        seed). LanguageModelError if the endpoint cannot be reached, times out or answers with
        an error."""
        ...
