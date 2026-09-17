"""Integration contract with a local OpenAI-compatible HTTP stub."""

import asyncio
import json

import httpx
import pytest

from backend.config import EmbeddingSettings
from backend.embeddings import EmbeddingClient

pytestmark = pytest.mark.integration


def test_client_contract_with_local_compatible_stub() -> None:
    received: dict[str, object] = {}

    async def scenario() -> list[list[float]]:
        async def stub(request: httpx.Request) -> httpx.Response:
            received["request"] = json.loads(request.content)
            received["path"] = f"{request.method} {request.url.path}"
            return httpx.Response(
                200,
                json={
                    "object": "list",
                    "data": [
                        {"object": "embedding", "index": 1, "embedding": [0.3, 0.4]},
                        {"object": "embedding", "index": 0, "embedding": [0.1, 0.2]},
                    ],
                },
            )

        settings = EmbeddingSettings(
            base_url="http://embedding.test/v1",
            model="contract-model",
            dimension=2,
            timeout_seconds=1,
        )
        async with (
            httpx.AsyncClient(transport=httpx.MockTransport(stub)) as http,
            EmbeddingClient(settings, http_client=http) as client,
        ):
            return await client.embed_batch(["first", "second"])

    assert asyncio.run(scenario()) == [[0.1, 0.2], [0.3, 0.4]]
    assert received == {
        "path": "POST /v1/embeddings",
        "request": {"model": "contract-model", "input": ["first", "second"]},
    }
