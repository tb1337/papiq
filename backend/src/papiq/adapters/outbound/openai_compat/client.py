"""OpenAI-compatible chat completions and embeddings over httpx2.

Works with Ollama (`http://host:11434/v1`) and cloud providers that follow the OpenAI API. The
base URL includes the version path; `/chat/completions` and `/embeddings` are appended.

- Structured output: `response_format` `json_schema` (strict) by default; `json_object` for
  providers that only promise JSON. The answer is not checked here (see the port).
- No retries of its own: failures raise, and the pipeline's retry policy repeats the step.
- Neither the API key nor document content is logged; errors carry the HTTP status and a short
  excerpt of the provider's message.
"""

import logging
from collections.abc import Sequence
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx2

from papiq.core.domain.errors import EmbeddingsError, LanguageModelError
from papiq.core.ports.embeddings import EmbeddingResult
from papiq.core.ports.llm import StructuredAnswer, StructuredRequest

log = logging.getLogger(__name__)

type ResponseFormat = Literal["json_schema", "json_object"]

_EXCERPT = 300  # characters of an error message from the provider


class OpenAiCompatLanguageModel:
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None = None,
        temperature: float = 0.0,
        seed: int | None = None,
        timeout: float = 300.0,
        response_format: ResponseFormat = "json_schema",
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        """`transport` replaces the network in tests."""
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._model = model
        self._headers = _headers(api_key)
        self._temperature = temperature
        self._seed = seed
        self._timeout = timeout
        self._format = response_format
        self._transport = transport
        self._endpoint = endpoint_of(base_url)

    @property
    def model(self) -> str:
        return self._model

    @property
    def endpoint(self) -> str:
        return self._endpoint

    async def complete(self, request: StructuredRequest) -> StructuredAnswer:
        body: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user},
            ],
            "temperature": self._temperature,
            "stream": False,
            "response_format": self._response_format(request),
        }
        if self._seed is not None:
            body["seed"] = self._seed
        data = await _post(
            self._url, body, self._headers, self._timeout, self._transport, LanguageModelError
        )
        try:
            choice = data["choices"][0]
            content = choice["message"].get("content")
            usage = data.get("usage") or {}
            return StructuredAnswer(
                content=content if isinstance(content, str) else "",
                model=str(data.get("model") or self._model),
                fingerprint=_optional_text(data.get("system_fingerprint")),
                prompt_tokens=_optional_int(usage.get("prompt_tokens")),
                completion_tokens=_optional_int(usage.get("completion_tokens")),
            )
        except (KeyError, IndexError, TypeError, AttributeError):
            raise LanguageModelError("the language model sent an unusable response") from None

    def _response_format(self, request: StructuredRequest) -> dict[str, Any]:
        if self._format == "json_object":
            return {"type": "json_object"}
        return {
            "type": "json_schema",
            "json_schema": {"name": request.schema_name, "schema": request.schema, "strict": True},
        }


class OpenAiCompatEmbeddings:
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None = None,
        timeout: float = 60.0,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        self._url = base_url.rstrip("/") + "/embeddings"
        self._model = model
        self._headers = _headers(api_key)
        self._timeout = timeout
        self._transport = transport

    @property
    def model(self) -> str:
        return self._model

    async def embed(self, texts: Sequence[str]) -> EmbeddingResult:
        if not texts:
            return EmbeddingResult(vectors=[], model=self._model)
        body = {"model": self._model, "input": list(texts)}
        data = await _post(
            self._url, body, self._headers, self._timeout, self._transport, EmbeddingsError
        )
        try:
            items = sorted(data["data"], key=lambda item: int(item["index"]))
            vectors = [tuple(float(value) for value in item["embedding"]) for item in items]
        except (KeyError, TypeError, ValueError):
            raise EmbeddingsError("the embedding model sent an unusable response") from None
        if [int(item["index"]) for item in items] != list(range(len(texts))):
            raise EmbeddingsError(f"expected {len(texts)} vectors, got {len(vectors)}")
        if len({len(vector) for vector in vectors}) != 1 or not vectors[0]:
            raise EmbeddingsError("the embedding model sent vectors of different length")
        return EmbeddingResult(vectors=vectors, model=str(data.get("model") or self._model))


def endpoint_of(base_url: str) -> str:
    """Host and port of a URL, without credentials or path."""
    parts = urlsplit(base_url)
    host = parts.hostname or ""
    if ":" in host:  # IPv6
        host = f"[{host}]"
    return f"{host}:{parts.port}" if parts.port else host


def _headers(api_key: str | None) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


async def _post(
    url: str,
    body: dict[str, Any],
    headers: dict[str, str],
    timeout: float,
    transport: httpx2.AsyncBaseTransport | None,
    error: type[LanguageModelError] | type[EmbeddingsError],
) -> dict[str, Any]:
    try:
        async with httpx2.AsyncClient(transport=transport, timeout=timeout) as client:
            response = await client.post(url, json=body, headers=headers)
    except httpx2.TimeoutException:
        raise error(f"no answer within {timeout:g} seconds") from None
    except httpx2.HTTPError as failure:
        raise error(f"cannot reach {endpoint_of(url)}: {type(failure).__name__}") from None
    if response.status_code >= 400:
        raise error(f"HTTP {response.status_code}: {_message(response)}")
    try:
        data = response.json()
    except ValueError:
        raise error("the response is not JSON") from None
    if not isinstance(data, dict):
        raise error("the response is not a JSON object")
    return data


def _message(response: httpx2.Response) -> str:
    """The provider's error message, shortened; the raw body if it is not the usual JSON."""
    try:
        data = response.json()
        detail = data["error"]["message"] if isinstance(data["error"], dict) else data["error"]
        text = str(detail)
    except (ValueError, KeyError, TypeError):
        text = response.text
    text = " ".join(text.split())
    return text[:_EXCERPT] or response.reason_phrase


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
