"""CI-safe contract checks for the optional vLLM embedding service."""

import json
import os
import re
import subprocess
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.unit

PROJECT_ROOT = Path(__file__).parents[3]
COMPOSE_FILE = PROJECT_ROOT / "docker-compose.yml"
MODEL = "ai-sage/Giga-Embeddings-instruct-480M-0826"


def service_definition(name: str) -> str:
    """Return one top-level Compose service definition as source text."""
    compose = COMPOSE_FILE.read_text(encoding="utf-8")
    marker = f"  {name}:\n"
    start = compose.index(marker) + len(marker)
    next_service = re.search(r"(?m)^  [a-zA-Z0-9_-]+:\n", compose[start:])
    end = len(compose) if next_service is None else start + next_service.start()
    return compose[start:end]


def test_vllm_service_is_gpu_only_and_persistent() -> None:
    service = service_definition("vllm-embeddings")

    assert 'profiles: ["gpu"]' in service
    assert "vllm/vllm-openai:" in service
    assert f"      - {MODEL}" in service
    assert "      - --task\n      - embed" in service
    assert "driver: nvidia" in service
    assert "capabilities: [gpu]" in service
    assert '"${GRAPH_BLIZZ_EMBEDDING_PORT:-8001}:8000"' in service
    assert "huggingface_cache:/root/.cache/huggingface" in service


def test_env_example_keeps_rag_api_on_compose_service_dns() -> None:
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            ".env.example",
            "--profile",
            "gpu",
            "config",
            "--format",
            "json",
        ],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    compose = json.loads(result.stdout)

    assert (
        compose["services"]["rag-api"]["environment"]["GRAPH_BLIZZ_EMBEDDING__BASE_URL"]
        == "http://vllm-embeddings:8000/v1"
    )


def test_rag_api_uses_host_ollama_defaults() -> None:
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            ".env.example",
            "config",
            "--format",
            "json",
        ],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    rag_api = json.loads(result.stdout)["services"]["rag-api"]

    assert rag_api["environment"]["GRAPH_BLIZZ_EXTERNAL_LLM__BASE_URL"] == (
        "http://host.docker.internal:11434/v1"
    )
    assert rag_api["environment"]["GRAPH_BLIZZ_EXTERNAL_LLM__MODEL"] == "qwen3:1.7b"
    assert rag_api["environment"]["GRAPH_BLIZZ_EXTERNAL_LLM__TIMEOUT_SECONDS"] == "60"
    assert rag_api["extra_hosts"] == ["host.docker.internal=host-gateway"]


def test_rag_api_forwards_external_embedding_api_key() -> None:
    environment = os.environ | {
        "GRAPH_BLIZZ_EMBEDDING__API_KEY": "test-embedding-provider-key",
    }
    result = subprocess.run(
        ["docker", "compose", "config", "--format", "json"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert (
        json.loads(result.stdout)["services"]["rag-api"]["environment"][
            "GRAPH_BLIZZ_EMBEDDING__API_KEY"
        ]
        == "test-embedding-provider-key"
    )


def test_rag_api_allows_external_llm_provider_overrides() -> None:
    environment = os.environ | {
        "GRAPH_BLIZZ_COMPOSE_EXTERNAL_LLM_BASE_URL": "https://llm.example/v1",
        "GRAPH_BLIZZ_EXTERNAL_LLM__MODEL": "provider/model",
        "GRAPH_BLIZZ_EXTERNAL_LLM__API_KEY": "test-provider-key",
        "GRAPH_BLIZZ_EXTERNAL_LLM__TIMEOUT_SECONDS": "15",
    }
    result = subprocess.run(
        ["docker", "compose", "config", "--format", "json"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    llm_environment = {
        name: value
        for name, value in json.loads(result.stdout)["services"]["rag-api"][
            "environment"
        ].items()
        if name.startswith("GRAPH_BLIZZ_EXTERNAL_LLM__")
    }

    assert llm_environment == {
        "GRAPH_BLIZZ_EXTERNAL_LLM__API_KEY": "test-provider-key",
        "GRAPH_BLIZZ_EXTERNAL_LLM__BASE_URL": "https://llm.example/v1",
        "GRAPH_BLIZZ_EXTERNAL_LLM__MODEL": "provider/model",
        "GRAPH_BLIZZ_EXTERNAL_LLM__TIMEOUT_SECONDS": "15",
    }


def test_openai_embedding_contract_with_local_stub() -> None:
    def stub(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/v1/embeddings"
        assert json.loads(request.content) == {
            "model": MODEL,
            "input": ["contract test"],
        }
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2]}],
                "model": MODEL,
                "usage": {"prompt_tokens": 2, "total_tokens": 2},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(stub)) as client:
        response = client.post(
            "http://vllm.test/v1/embeddings",
            json={"model": MODEL, "input": ["contract test"]},
        )
    response.raise_for_status()
    body = response.json()

    assert body["object"] == "list"
    assert body["model"] == MODEL
    assert body["data"][0]["object"] == "embedding"
    assert body["data"][0]["index"] == 0
    assert body["data"][0]["embedding"] == [0.1, 0.2]
