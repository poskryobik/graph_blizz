"""Real-storage integration coverage for the LightRAG production factory."""

import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration
PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def compose_project() -> Iterator[tuple[str, dict[str, str]]]:
    """Start isolated production storage services and remove their volumes."""
    if shutil.which("docker") is None:
        pytest.skip("Docker CLI is not installed")

    for command in (["docker", "compose", "version"], ["docker", "info"]):
        availability = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if availability.returncode != 0:
            pytest.skip(f"{' '.join(command)} is unavailable")

    project = f"graph_blizz_f020_{os.getpid()}"
    environment = os.environ.copy()
    environment.update(
        {
            "GRAPH_BLIZZ_API_PORT": "0",
            "GRAPH_BLIZZ_MINIO_API_PORT": "0",
            "GRAPH_BLIZZ_MINIO_CONSOLE_PORT": "0",
            "GRAPH_BLIZZ_QDRANT_PORT": "0",
            "GRAPH_BLIZZ_POSTGRES__PASSWORD": "f020-postgres-password",
            "GRAPH_BLIZZ_MINIO__ACCESS_KEY": "f020-access-key",
            "GRAPH_BLIZZ_MINIO__SECRET_KEY": "f020-minio-password",
            "GRAPH_BLIZZ_NEO4J__PASSWORD": "f020-neo4j-password",
        }
    )

    try:
        _compose(project, environment, "up", "-d", "--build", "--wait", timeout=420)
        yield project, environment
    finally:
        _compose(
            project,
            environment,
            "down",
            "--volumes",
            "--remove-orphans",
            check=False,
            timeout=120,
        )


def test_real_storages_support_minimal_insert_and_query(
    compose_project: tuple[str, dict[str, str]],
) -> None:
    """Insert and query through LightRAG with real PostgreSQL/Qdrant/Neo4j."""
    project, environment = compose_project
    script = r"""
import asyncio
import hashlib
import httpx
from lightrag import QueryParam
from backend.config import ApplicationSettings
from backend.embeddings import EmbeddingClient
from backend.llm import LLMClient
from backend.rag import create_lightrag_runtime

async def embedding_stub(request: httpx.Request) -> httpx.Response:
    payload = __import__("json").loads(request.content)
    data = []
    for index, text in enumerate(payload["input"]):
        digest = hashlib.sha256(text.encode()).digest()
        data.append({"index": index, "embedding": [
            digest[0] / 255, digest[1] / 255, digest[2] / 255
        ]})
    return httpx.Response(200, json={"data": data})

async def llm_stub(request: httpx.Request) -> httpx.Response:
    payload = __import__("json").loads(request.content)
    prompt = "\n".join(message["content"] for message in payload["messages"])
    if "identify and extract any missed" in prompt:
        content = '{"entities": [], "relationships": []}'
    elif "Strict Adherence to JSON Format" in prompt and "Input Text" in prompt:
        content = '''{"entities": [
            {"name": "F020 Marker", "type": "Concept", "description": "A verification marker."},
            {"name": "LightRAG Storage Wiring", "type": "Technology", "description": "The configured storage path."}
        ], "relationships": [
            {"source": "F020 Marker", "target": "LightRAG Storage Wiring", "keywords": "proves", "description": "The marker proves the wiring."}
        ]}'''
    else:
        content = "F020 storage wiring answer"
    return httpx.Response(200, json={"choices": [{
        "message": {"role": "assistant", "content": content}
    }]})

async def main() -> None:
    settings = ApplicationSettings()
    settings = settings.model_copy(update={
        "embedding": settings.embedding.model_copy(update={"dimension": 3})
    })
    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(embedding_stub)) as embed_http,
        httpx.AsyncClient(transport=httpx.MockTransport(llm_stub)) as llm_http,
    ):
        embedding = EmbeddingClient(settings.embedding, http_client=embed_http)
        llm = LLMClient(settings.external_llm, http_client=llm_http)
        async with await create_lightrag_runtime(
            settings,
            workspace="ws_f020_integration",
            embedding_client=embedding,
            llm_client=llm,
        ) as runtime:
            assert type(runtime.rag.full_docs).__name__ == "PGKVStorage"
            assert type(runtime.rag.doc_status).__name__ == "PGDocStatusStorage"
            assert type(runtime.rag.chunks_vdb).__name__ == "QdrantVectorDBStorage"
            assert type(runtime.rag.chunk_entity_relation_graph).__name__ == "Neo4JStorage"
            await runtime.rag.ainsert(
                "The F020 marker proves LightRAG storage wiring.",
                ids="f020-document",
            )
            graph = runtime.rag.chunk_entity_relation_graph
            nodes = await graph.get_all_nodes()
            edges = await graph.get_all_edges()
            node_ids = {node["id"] for node in nodes}
            assert {"F020 Marker", "LightRAG Storage Wiring"} <= node_ids
            assert await graph.get_node("F020 Marker") is not None
            assert await graph.get_node("LightRAG Storage Wiring") is not None
            assert await graph.has_edge("F020 Marker", "LightRAG Storage Wiring")
            assert any(
                {edge["source"], edge["target"]}
                == {"F020 Marker", "LightRAG Storage Wiring"}
                for edge in edges
            )
            answer = await runtime.rag.aquery(
                "What does the F020 marker prove?",
                param=QueryParam(mode="naive", enable_rerank=False),
            )
            assert answer == "F020 storage wiring answer"

asyncio.run(main())
"""
    _compose(
        project,
        environment,
        "exec",
        "-T",
        "rag-api",
        "/app/.venv/bin/python",
        "-c",
        script,
        timeout=180,
    )


def _compose(
    project: str,
    environment: dict[str, str],
    *arguments: str,
    check: bool = True,
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    """Run Docker Compose and surface diagnostics on failure."""
    result = subprocess.run(
        ["docker", "compose", "--project-name", project, *arguments],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )
    if check and result.returncode != 0:
        pytest.fail(
            f"docker compose {' '.join(arguments)} failed:\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result
