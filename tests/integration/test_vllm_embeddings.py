"""Opt-in verification of an explicitly configured live vLLM endpoint."""

import json
import os
import urllib.error
import urllib.request

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.gpu]

MODEL = "ai-sage/Giga-Embeddings-instruct-480M-0826"


def test_vllm_returns_openai_compatible_embedding() -> None:
    base_url = os.getenv("GRAPH_BLIZZ_EMBEDDING_LIVE_BASE_URL")
    if not base_url:
        pytest.skip(
            "set GRAPH_BLIZZ_EMBEDDING_LIVE_BASE_URL to a live OpenAI-compatible "
            "embedding endpoint"
        )

    model = os.getenv("GRAPH_BLIZZ_EMBEDDING__MODEL", MODEL)
    payload = json.dumps({"model": model, "input": ["Граф знаний"]}).encode()
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/embeddings",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            body = json.load(response)
    except (OSError, urllib.error.HTTPError) as error:
        pytest.fail(f"vLLM embedding endpoint is unavailable or invalid: {error}")

    assert body["object"] == "list"
    assert body["model"] == model
    assert len(body["data"]) == 1
    assert body["data"][0]["object"] == "embedding"
    assert body["data"][0]["index"] == 0
    embedding = body["data"][0]["embedding"]
    assert isinstance(embedding, list)
    assert len(embedding) > 0
    assert all(isinstance(value, (int, float)) for value in embedding)
