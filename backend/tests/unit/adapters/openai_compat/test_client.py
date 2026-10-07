"""The OpenAI-compatible adapter against a simulated endpoint: the contracts, the requests it
sends and how it handles errors. No network."""

import hashlib
import json
from collections.abc import Callable
from typing import Any

import httpx2
import pytest

from papiq.adapters.outbound.openai_compat import (
    OpenAiCompatEmbeddings,
    OpenAiCompatLanguageModel,
    endpoint_of,
)
from papiq.core.domain.errors import EmbeddingsError, LanguageModelError
from tests.contracts.language_model import REQUEST, EmbeddingsContract, LanguageModelContract

BASE_URL = "http://ollama:11434/v1"

type Handler = Callable[[httpx2.Request], httpx2.Response]


class SimulatedEndpoint:
    """Answers chat completions with `content` and embeddings with hash vectors; records
    every request."""

    def __init__(self, content: str = '{"answer": "ok"}') -> None:
        self.content = content
        self.requests: list[httpx2.Request] = []

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        body = json.loads(request.content)
        if request.url.path.endswith("/chat/completions"):
            return httpx2.Response(
                200,
                json={
                    "model": body["model"] + ":latest",
                    "system_fingerprint": "fp_ollama",
                    "choices": [{"message": {"role": "assistant", "content": self.content}}],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 5},
                },
            )
        if request.url.path.endswith("/embeddings"):
            data = [
                {"index": index, "embedding": _vector(text)}
                for index, text in reversed(list(enumerate(body["input"])))
            ]
            return httpx2.Response(200, json={"model": body["model"], "data": data})
        return httpx2.Response(404, json={"error": "not found"})

    @property
    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self.handle)

    def body(self, index: int = -1) -> Any:
        return json.loads(self.requests[index].content)


def _vector(text: str) -> list[float]:
    return [byte / 255 for byte in hashlib.sha256(text.encode()).digest()[:8]]


def failing(status: int = 503, **body: Any) -> httpx2.MockTransport:
    return httpx2.MockTransport(lambda request: httpx2.Response(status, json=body or None))


def language_model(
    transport: httpx2.AsyncBaseTransport, **options: Any
) -> OpenAiCompatLanguageModel:
    return OpenAiCompatLanguageModel(
        base_url=BASE_URL, model="qwen3", transport=transport, **options
    )


def embeddings_for(transport: httpx2.AsyncBaseTransport, **options: Any) -> OpenAiCompatEmbeddings:
    return OpenAiCompatEmbeddings(base_url=BASE_URL, model="bge-m3", transport=transport, **options)


@pytest.fixture
def endpoint() -> SimulatedEndpoint:
    return SimulatedEndpoint()


@pytest.fixture(name="language_model")
def language_model_fixture(endpoint: SimulatedEndpoint) -> OpenAiCompatLanguageModel:
    return language_model(endpoint.transport)


@pytest.fixture
def failing_language_model() -> OpenAiCompatLanguageModel:
    return language_model(failing())


@pytest.fixture
def embeddings(endpoint: SimulatedEndpoint) -> OpenAiCompatEmbeddings:
    return embeddings_for(endpoint.transport)


@pytest.fixture
def failing_embeddings() -> OpenAiCompatEmbeddings:
    return embeddings_for(failing())


class TestOpenAiCompatLanguageModel(LanguageModelContract):
    pass


class TestOpenAiCompatEmbeddings(EmbeddingsContract):
    pass


async def test_sends_a_strict_json_schema_request(endpoint: SimulatedEndpoint) -> None:
    model = language_model(endpoint.transport, api_key="sk-secret", seed=7)
    answer = await model.complete(REQUEST)

    request = endpoint.requests[0]
    assert str(request.url) == BASE_URL + "/chat/completions"
    assert request.headers["authorization"] == "Bearer sk-secret"
    body = endpoint.body()
    assert body["model"] == "qwen3"
    assert body["messages"] == [
        {"role": "system", "content": REQUEST.system},
        {"role": "user", "content": REQUEST.user},
    ]
    assert body["temperature"] == 0
    assert body["seed"] == 7
    assert body["stream"] is False
    assert body["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "contract", "schema": REQUEST.schema, "strict": True},
    }
    assert answer.model == "qwen3:latest"
    assert answer.fingerprint == "fp_ollama"
    assert (answer.prompt_tokens, answer.completion_tokens) == (12, 5)


async def test_json_object_format_and_no_key(endpoint: SimulatedEndpoint) -> None:
    model = language_model(endpoint.transport, response_format="json_object", temperature=0.3)
    await model.complete(REQUEST)

    assert "authorization" not in endpoint.requests[0].headers
    body = endpoint.body()
    assert body["response_format"] == {"type": "json_object"}
    assert body["temperature"] == 0.3
    assert "seed" not in body


async def test_the_answer_is_returned_unchecked(endpoint: SimulatedEndpoint) -> None:
    endpoint.content = "not json at all"
    answer = await language_model(endpoint.transport).complete(REQUEST)
    assert answer.content == "not json at all"


async def test_a_missing_content_is_empty_text() -> None:
    def handle(request: httpx2.Request) -> httpx2.Response:
        message = {"role": "assistant", "content": None, "refusal": "no"}
        return httpx2.Response(200, json={"choices": [{"message": message}]})

    answer = await language_model(httpx2.MockTransport(handle)).complete(REQUEST)
    assert answer.content == ""
    assert answer.model == "qwen3"
    assert answer.fingerprint is None


@pytest.mark.parametrize(
    ("handler", "message"),
    [
        (
            lambda request: httpx2.Response(
                400, json={"error": {"message": "model 'qwen3' not found"}}
            ),
            "HTTP 400: model 'qwen3' not found",
        ),
        (lambda request: httpx2.Response(500, text="boom\n  boom"), "HTTP 500: boom boom"),
        (lambda request: httpx2.Response(200, text="<html>"), "not JSON"),
        (lambda request: httpx2.Response(200, json=[1]), "not a JSON object"),
        (lambda request: httpx2.Response(200, json={"choices": []}), "unusable response"),
    ],
)
async def test_errors_of_the_endpoint(handler: Handler, message: str) -> None:
    with pytest.raises(LanguageModelError, match=message):
        await language_model(httpx2.MockTransport(handler)).complete(REQUEST)


async def test_unreachable_and_slow_endpoints() -> None:
    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=request)

    def slow(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("slow", request=request)

    with pytest.raises(LanguageModelError, match="cannot reach ollama:11434: ConnectError"):
        await language_model(httpx2.MockTransport(refuse)).complete(REQUEST)
    with pytest.raises(LanguageModelError, match=r"no answer within 2\.5 seconds"):
        await language_model(httpx2.MockTransport(slow), timeout=2.5).complete(REQUEST)


async def test_errors_do_not_show_the_key() -> None:
    def echo(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(401, json={"error": {"message": "invalid key"}})

    model = language_model(httpx2.MockTransport(echo), api_key="sk-secret")
    with pytest.raises(LanguageModelError) as info:
        await model.complete(REQUEST)
    assert "sk-secret" not in str(info.value)


async def test_embeddings_request_and_order(endpoint: SimulatedEndpoint) -> None:
    result = await embeddings_for(endpoint.transport, api_key="sk-e").embed(["a", "b"])

    request = endpoint.requests[0]
    assert str(request.url) == BASE_URL + "/embeddings"
    assert request.headers["authorization"] == "Bearer sk-e"
    assert endpoint.body() == {"model": "bge-m3", "input": ["a", "b"]}
    assert result.vectors == [tuple(_vector("a")), tuple(_vector("b"))]


async def test_no_texts_send_no_request(endpoint: SimulatedEndpoint) -> None:
    assert (await embeddings_for(endpoint.transport).embed([])).vectors == []
    assert endpoint.requests == []


@pytest.mark.parametrize(
    "data",
    [
        [{"index": 0, "embedding": [0.1]}],
        [{"index": 0, "embedding": [0.1]}, {"index": 1, "embedding": [0.1, 0.2]}],
        [{"index": 0, "embedding": []}, {"index": 1, "embedding": []}],
        [{"embedding": [0.1]}, {"embedding": [0.2]}],
    ],
)
async def test_unusable_embeddings_are_refused(data: list[dict[str, Any]]) -> None:
    transport = httpx2.MockTransport(lambda request: httpx2.Response(200, json={"data": data}))
    with pytest.raises(EmbeddingsError):
        await embeddings_for(transport).embed(["a", "b"])


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://ollama:11434/v1", "ollama:11434"),
        ("https://user:pw@api.example.com/v1", "api.example.com"),
        ("http://[::1]:11434/v1", "[::1]:11434"),
    ],
)
def test_endpoint_names_host_and_port(url: str, expected: str) -> None:
    assert endpoint_of(url) == expected
