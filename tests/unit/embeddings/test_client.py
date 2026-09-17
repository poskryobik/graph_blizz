"""Unit contract for the shared embedding client."""

import asyncio
import json
from typing import Any

import httpx
import pytest

from backend.config import EmbeddingSettings
from backend.embeddings import (
    EmbeddingClient,
    EmbeddingHTTPError,
    EmbeddingProtocolError,
    EmbeddingTimeoutError,
    EmbeddingTransportError,
)

pytestmark = pytest.mark.unit


def run_request(
    response: dict[str, Any] | httpx.Response | Exception,
    texts: list[str] | None = None,
) -> list[list[float]]:
    """Execute one batch against a deterministic in-memory HTTP transport."""

    async def scenario() -> list[list[float]]:
        async def stub(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/v1/embeddings"
            assert request.headers["Authorization"] == "Bearer test-secret"
            assert json.loads(request.content) == {
                "model": "test-model",
                "input": texts or ["first", "second"],
            }
            if isinstance(response, Exception):
                raise response
            if isinstance(response, httpx.Response):
                return response
            return httpx.Response(200, json=response)

        settings = EmbeddingSettings(
            base_url="http://embedding.test/v1",
            model="test-model",
            api_key="test-secret",
            dimension=2,
            timeout_seconds=0.25,
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(stub)) as http:
            client = EmbeddingClient(settings, http_client=http)
            return await client.embed_batch(texts or ["first", "second"])

    return asyncio.run(scenario())


def test_batch_restores_input_order_from_response_indices() -> None:
    payload = {
        "data": [
            {"index": 1, "embedding": [2, 2.5]},
            {"index": 0, "embedding": [1.0, 1]},
        ]
    }

    assert run_request(payload) == [[1.0, 1.0], [2.0, 2.5]]


def test_single_embedding_uses_the_same_batch_api() -> None:
    async def scenario() -> list[float]:
        async def stub(request: httpx.Request) -> httpx.Response:
            assert json.loads(request.content)["input"] == ["one"]
            return httpx.Response(
                200, json={"data": [{"index": 0, "embedding": [1, 2]}]}
            )

        settings = EmbeddingSettings(model="test-model", dimension=2)
        async with httpx.AsyncClient(transport=httpx.MockTransport(stub)) as http:
            return await EmbeddingClient(settings, http_client=http).embed("one")

    assert asyncio.run(scenario()) == [1.0, 2.0]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"data": [{"index": 0, "embedding": [1, 2]}]},
        {
            "data": [
                {"index": 0, "embedding": [1, 2]},
                {"index": 0, "embedding": [3, 4]},
            ]
        },
        {"data": [{"index": 0, "embedding": [1]}, {"index": 1, "embedding": [3, 4]}]},
        {
            "data": [
                {"index": 0, "embedding": [1, "NaN"]},
                {"index": 1, "embedding": [3, 4]},
            ]
        },
    ],
)
def test_malformed_responses_have_stable_protocol_error(
    payload: dict[str, Any],
) -> None:
    with pytest.raises(EmbeddingProtocolError):
        run_request(payload)


def test_http_status_has_stable_domain_error() -> None:
    with pytest.raises(EmbeddingHTTPError) as captured:
        run_request(httpx.Response(503))

    assert captured.value.status_code == 503


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (httpx.ReadTimeout("slow"), EmbeddingTimeoutError),
        (httpx.ConnectError("offline"), EmbeddingTransportError),
    ],
)
def test_transport_failures_have_stable_domain_errors(
    failure: Exception, expected: type[EmbeddingTransportError]
) -> None:
    with pytest.raises(expected):
        run_request(failure)


def test_empty_batch_does_not_call_endpoint() -> None:
    async def scenario() -> list[list[float]]:
        async def unexpected(_request: httpx.Request) -> httpx.Response:
            pytest.fail("empty batch must not make an HTTP request")

        settings = EmbeddingSettings(dimension=2)
        async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as http:
            return await EmbeddingClient(settings, http_client=http).embed_batch([])

    assert asyncio.run(scenario()) == []


def test_runtime_has_no_local_model_dependency_or_loader() -> None:
    source = __import__("inspect").getsource(
        __import__("backend.embeddings.client", fromlist=["*"])
    )
    forbidden = ("torch", "transformers", "sentence_transformers", "from_pretrained")

    assert all(name not in source for name in forbidden)
