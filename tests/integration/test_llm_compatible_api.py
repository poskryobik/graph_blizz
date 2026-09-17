"""Integration contract with a local OpenAI-compatible HTTP stub."""

import asyncio
import json

import httpx
import pytest

from backend.config import ExternalLLMSettings
from backend.llm import LLMClient

pytestmark = pytest.mark.integration


def test_extraction_and_generation_share_one_compatible_model() -> None:
    received: list[dict[str, object]] = []

    async def scenario() -> tuple[str, str]:
        async def stub(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            received.append(payload)
            assert f"{request.method} {request.url.path}" == "POST /v1/chat/completions"
            operation = payload["messages"][0]["content"]
            return httpx.Response(
                200,
                json={
                    "id": "chatcmpl-local",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": f"completed:{operation}",
                            },
                        }
                    ],
                },
            )

        settings = ExternalLLMSettings(
            base_url="http://llm.test/v1",
            model="shared-model",
            timeout_seconds=1,
        )
        async with (
            httpx.AsyncClient(transport=httpx.MockTransport(stub)) as http,
            LLMClient(settings, http_client=http) as client,
        ):
            extraction = await client.complete(
                [{"role": "system", "content": "extract"}]
            )
            generation = await client.complete(
                [{"role": "system", "content": "generate"}]
            )
            return extraction, generation

    assert asyncio.run(scenario()) == ("completed:extract", "completed:generate")
    assert [request["model"] for request in received] == [
        "shared-model",
        "shared-model",
    ]
