"""Unit contract for the shared OpenAI-compatible LLM client."""

import asyncio
import json
from typing import Any

import httpx
import pytest

from backend.config import ExternalLLMSettings
from backend.llm import (
    LLMClient,
    LLMHTTPError,
    LLMProtocolError,
    LLMTimeoutError,
    LLMTransportError,
)

pytestmark = pytest.mark.unit
MESSAGES = [
    {"role": "system", "content": "Answer briefly."},
    {"role": "user", "content": "What is a graph?", "name": "extractor"},
]


def run_request(response: dict[str, Any] | httpx.Response | Exception) -> str:
    """Execute one completion against a deterministic in-memory transport."""

    async def scenario() -> str:
        async def stub(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/v1/chat/completions"
            assert request.headers["Authorization"] == "Bearer test-secret"
            assert json.loads(request.content) == {
                "model": "test-model",
                "messages": MESSAGES,
            }
            if isinstance(response, Exception):
                raise response
            if isinstance(response, httpx.Response):
                return response
            return httpx.Response(200, json=response)

        settings = ExternalLLMSettings(
            base_url="http://llm.test/v1",
            model="test-model",
            api_key="test-secret",
            timeout_seconds=0.25,
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(stub)) as http:
            return await LLMClient(settings, http_client=http).complete(MESSAGES)

    return asyncio.run(scenario())


def test_returns_first_assistant_content_and_preserves_messages() -> None:
    payload = {
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": "A network."}},
            {"index": 1, "message": {"role": "assistant", "content": "Unused."}},
        ]
    }

    assert run_request(payload) == "A network."


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"choices": []},
        {"choices": [None]},
        {"choices": [{"message": None}]},
        {"choices": [{"message": {"role": "user", "content": "wrong"}}]},
        {"choices": [{"message": {"role": "assistant", "content": None}}]},
    ],
)
def test_malformed_responses_have_stable_protocol_error(
    payload: dict[str, Any],
) -> None:
    with pytest.raises(LLMProtocolError):
        run_request(payload)


def test_non_json_response_has_stable_protocol_error() -> None:
    with pytest.raises(LLMProtocolError, match="valid JSON"):
        run_request(httpx.Response(200, content=b"not-json"))


def test_http_status_has_stable_domain_error() -> None:
    with pytest.raises(LLMHTTPError) as captured:
        run_request(httpx.Response(429))

    assert captured.value.status_code == 429


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (httpx.ReadTimeout("slow"), LLMTimeoutError),
        (httpx.ConnectError("offline"), LLMTransportError),
    ],
)
def test_transport_failures_have_stable_domain_errors(
    failure: Exception, expected: type[LLMTransportError]
) -> None:
    with pytest.raises(expected):
        run_request(failure)


def test_runtime_has_no_local_model_dependency_or_loader() -> None:
    source = __import__("inspect").getsource(
        __import__("backend.llm.client", fromlist=["*"])
    )
    forbidden = ("torch", "transformers", "from_pretrained", "load_model")

    assert all(name not in source for name in forbidden)
