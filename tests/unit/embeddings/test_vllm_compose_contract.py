"""CI-safe contract checks for the external-only vLLM embedding endpoint."""

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import httpx
import pytest

pytestmark = pytest.mark.unit

PROJECT_ROOT = Path(__file__).parents[3]
COMPOSE_FILE = PROJECT_ROOT / "docker-compose.yml"
MODEL = "ai-sage/Giga-Embeddings-instruct-480M-0826"
EMBEDDING_PREFIX = "GRAPH_BLIZZ_EMBEDDING__"


def compose_text() -> str:
    """Return the committed ``docker-compose.yml`` source text."""
    return COMPOSE_FILE.read_text(encoding="utf-8")


def service_definition(name: str) -> str:
    """Return one top-level Compose service definition as source text."""
    compose = compose_text()
    marker = f"  {name}:\n"
    start = compose.index(marker) + len(marker)
    next_service = re.search(r"(?m)^  [a-zA-Z0-9_-]+:\n", compose[start:])
    end = len(compose) if next_service is None else start + next_service.start()
    return compose[start:end]


def declared_embedding_environment(name: str) -> dict[str, str]:
    """Return the embedding variables declared for one Compose service."""
    return {
        key.strip(): value.strip()
        for line in service_definition(name).splitlines()
        if line.strip().startswith(EMBEDDING_PREFIX)
        for key, _, value in [line.strip().partition(":")]
    }


def rendered_config(overrides: dict[str, str] | None = None) -> dict[str, Any]:
    """Render ``docker-compose.yml`` without ambient Graph Blizz variables."""
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith("GRAPH_BLIZZ_")
    }
    environment.update(overrides or {})
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            COMPOSE_FILE.name,
            "--env-file",
            os.devnull,
            "config",
            "--format",
            "json",
        ],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    return json.loads(result.stdout)


def embedding_environment(config: dict[str, Any], name: str) -> dict[str, str | None]:
    """Return the rendered embedding variables of one Compose service."""
    return {
        key: value
        for key, value in config["services"][name]["environment"].items()
        if key.startswith(EMBEDDING_PREFIX)
    }


def test_compose_declares_no_bundled_embedding_service_or_gpu_resources() -> None:
    compose = compose_text()

    assert "vllm-embeddings" not in compose
    assert "vllm/vllm-openai" not in compose
    assert 'profiles: ["gpu"]' not in compose
    assert "driver: nvidia" not in compose
    assert "capabilities: [gpu]" not in compose
    assert "huggingface_cache" not in compose
    assert "GRAPH_BLIZZ_EMBEDDING_PORT" not in compose


def test_both_processes_declare_identical_embedding_environment() -> None:
    rag_api = declared_embedding_environment("rag-api")
    rag_worker = declared_embedding_environment("rag-worker")

    assert rag_api == rag_worker
    assert set(rag_api) == {
        "GRAPH_BLIZZ_EMBEDDING__API_KEY",
        "GRAPH_BLIZZ_EMBEDDING__BASE_URL",
        "GRAPH_BLIZZ_EMBEDDING__DIMENSION",
        "GRAPH_BLIZZ_EMBEDDING__MODEL",
        "GRAPH_BLIZZ_EMBEDDING__TIMEOUT_SECONDS",
    }


def test_default_embedding_base_url_is_external() -> None:
    declared = declared_embedding_environment("rag-api")[
        "GRAPH_BLIZZ_EMBEDDING__BASE_URL"
    ]

    assert declared == (
        "${GRAPH_BLIZZ_COMPOSE_EMBEDDING_BASE_URL:-http://host.docker.internal:8000/v1}"
    )
    for name in ("rag-api", "rag-worker"):
        base_url = embedding_environment(rendered_config(), name)[
            "GRAPH_BLIZZ_EMBEDDING__BASE_URL"
        ]
        assert base_url == "http://host.docker.internal:8000/v1"


def test_embedding_defaults_are_shared_by_both_processes() -> None:
    config = rendered_config()

    for name in ("rag-api", "rag-worker"):
        environment = embedding_environment(config, name)
        assert environment["GRAPH_BLIZZ_EMBEDDING__MODEL"] == MODEL
        assert environment["GRAPH_BLIZZ_EMBEDDING__DIMENSION"] == "1024"
        assert environment["GRAPH_BLIZZ_EMBEDDING__TIMEOUT_SECONDS"] == "30"


def test_embedding_api_key_is_unset_without_configuration() -> None:
    config = rendered_config()

    for name in ("rag-api", "rag-worker"):
        api_key = embedding_environment(config, name).get(
            "GRAPH_BLIZZ_EMBEDDING__API_KEY"
        )
        assert api_key is None, f"{name} received a blank embedding API key"


def test_embedding_overrides_propagate_to_both_processes() -> None:
    config = rendered_config(
        {
            "GRAPH_BLIZZ_COMPOSE_EMBEDDING_BASE_URL": "https://embedding.example/v1",
            "GRAPH_BLIZZ_EMBEDDING__MODEL": "provider/model",
            "GRAPH_BLIZZ_EMBEDDING__DIMENSION": "768",
            "GRAPH_BLIZZ_EMBEDDING__TIMEOUT_SECONDS": "15",
            "GRAPH_BLIZZ_EMBEDDING__API_KEY": "test-embedding-provider-key",
        }
    )
    expected = {
        "GRAPH_BLIZZ_EMBEDDING__API_KEY": "test-embedding-provider-key",
        "GRAPH_BLIZZ_EMBEDDING__BASE_URL": "https://embedding.example/v1",
        "GRAPH_BLIZZ_EMBEDDING__DIMENSION": "768",
        "GRAPH_BLIZZ_EMBEDDING__MODEL": "provider/model",
        "GRAPH_BLIZZ_EMBEDDING__TIMEOUT_SECONDS": "15",
    }

    assert embedding_environment(config, "rag-api") == expected
    assert embedding_environment(config, "rag-worker") == expected


def test_rag_api_uses_host_ollama_defaults() -> None:
    rag_api = rendered_config()["services"]["rag-api"]

    assert rag_api["environment"]["GRAPH_BLIZZ_EXTERNAL_LLM__BASE_URL"] == (
        "http://host.docker.internal:11434/v1"
    )
    assert rag_api["environment"]["GRAPH_BLIZZ_EXTERNAL_LLM__MODEL"] == "qwen3:1.7b"
    assert rag_api["environment"]["GRAPH_BLIZZ_EXTERNAL_LLM__TIMEOUT_SECONDS"] == "60"
    assert rag_api["extra_hosts"] == ["host.docker.internal=host-gateway"]


def test_rag_api_allows_external_llm_provider_overrides() -> None:
    config = rendered_config(
        {
            "GRAPH_BLIZZ_COMPOSE_EXTERNAL_LLM_BASE_URL": "https://llm.example/v1",
            "GRAPH_BLIZZ_EXTERNAL_LLM__MODEL": "provider/model",
            "GRAPH_BLIZZ_EXTERNAL_LLM__API_KEY": "test-provider-key",
            "GRAPH_BLIZZ_EXTERNAL_LLM__TIMEOUT_SECONDS": "15",
        }
    )
    llm_environment = {
        name: value
        for name, value in config["services"]["rag-api"]["environment"].items()
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
