"""Prove physical data isolation for two Demo-owner workspaces."""

import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest

pytestmark = pytest.mark.e2e
PROJECT_ROOT = Path(__file__).resolve().parents[2]

FACT_A = "The Crimson Heron observatory stores lunar maps on Amber Atoll."
FACT_B = "The Silver Badger archive keeps tidal charts on Cobalt Cay."
MARKER_A = "Crimson Heron"
MARKER_B = "Silver Badger"
FILENAME_A = "crimson-heron.txt"
FILENAME_B = "silver-badger.txt"


@dataclass(frozen=True, slots=True)
class _ComposeStack:
    api_url: str
    project: str
    environment: dict[str, str]


class _ModelHandler(BaseHTTPRequestHandler):
    """Provide deterministic OpenAI-compatible models to the Compose services."""

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length))
        if self.path == "/v1/embeddings":
            self._respond(
                {
                    "data": [
                        {"index": index, "embedding": _embedding(text)}
                        for index, text in enumerate(payload["input"])
                    ]
                }
            )
            return
        if self.path == "/v1/chat/completions":
            prompt = "\n".join(
                str(message.get("content", "")) for message in payload["messages"]
            )
            if "identify and extract any missed" in prompt:
                content = '{"entities": [], "relationships": []}'
            elif "Strict Adherence to JSON Format" in prompt and "Input Text" in prompt:
                content = json.dumps(_extraction_for(prompt))
            elif FACT_A in prompt:
                content = FACT_A
            elif FACT_B in prompt:
                content = FACT_B
            else:
                content = "The requested fact is not present in this workspace."
            self._respond(
                {"choices": [{"message": {"role": "assistant", "content": content}}]}
            )
            return
        self.send_error(404)

    def log_message(self, _format: str, *args: object) -> None:
        """Keep successful test output quiet."""

    def _respond(self, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _extraction_for(prompt: str) -> dict[str, list[dict[str, str]]]:
    if MARKER_A in prompt:
        subject, location, fact = MARKER_A, "Amber Atoll", FACT_A
    elif MARKER_B in prompt:
        subject, location, fact = MARKER_B, "Cobalt Cay", FACT_B
    else:
        return {"entities": [], "relationships": []}
    return {
        "entities": [
            {"name": subject, "type": "place", "description": fact},
            {"name": location, "type": "place", "description": fact},
        ],
        "relationships": [
            {
                "source": subject,
                "target": location,
                "keywords": "located on",
                "description": fact,
            }
        ],
    }


def _embedding(text: str) -> list[float]:
    digest = hashlib.sha256(text.encode()).digest()
    return [digest[index] / 255 for index in range(3)]


@pytest.fixture(scope="module")
def compose_available() -> None:
    """Skip only when the external Compose prerequisite is unavailable."""
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


@pytest.fixture(scope="module")
def model_server(compose_available: None) -> Iterator[int]:
    """Run deterministic model endpoints reachable from Docker containers."""
    server = ThreadingHTTPServer(("0.0.0.0", 0), _ModelHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture(scope="module")
def demo_stack(model_server: int) -> Iterator[_ComposeStack]:
    """Start an isolated clean Compose stack and expose inspection credentials."""
    project = f"graph_blizz_f039_{os.getpid()}"
    environment = os.environ.copy()
    environment.update(
        {
            "GRAPH_BLIZZ_API_PORT": "0",
            "GRAPH_BLIZZ_MINIO_API_PORT": "0",
            "GRAPH_BLIZZ_MINIO_CONSOLE_PORT": "0",
            "GRAPH_BLIZZ_QDRANT_PORT": "0",
            "GRAPH_BLIZZ_POSTGRES__PASSWORD": "f039-postgres-password",
            "GRAPH_BLIZZ_MINIO__ACCESS_KEY": "f039-access-key",
            "GRAPH_BLIZZ_MINIO__SECRET_KEY": "f039-minio-password",
            "GRAPH_BLIZZ_NEO4J__PASSWORD": "f039-neo4j-password",
            "GRAPH_BLIZZ_EMBEDDING__DIMENSION": "3",
            "GRAPH_BLIZZ_COMPOSE_EMBEDDING_BASE_URL": (
                f"http://host.docker.internal:{model_server}/v1"
            ),
            "GRAPH_BLIZZ_COMPOSE_EXTERNAL_LLM_BASE_URL": (
                f"http://host.docker.internal:{model_server}/v1"
            ),
        }
    )

    try:
        _compose(project, environment, "up", "-d", "--build", "--wait", timeout=420)
        _compose(
            project,
            environment,
            "exec",
            "-T",
            "rag-api",
            "/app/.venv/bin/alembic",
            "upgrade",
            "head",
            timeout=60,
        )
        _compose(
            project,
            environment,
            "exec",
            "-T",
            "rag-api",
            "/app/.venv/bin/python",
            "-c",
            _CREATE_BUCKET,
            timeout=30,
        )
        published = _compose(
            project, environment, "port", "rag-api", "8000", timeout=30
        ).stdout.splitlines()[0]
        yield _ComposeStack(
            api_url=f"http://127.0.0.1:{published.rsplit(':', 1)[1]}",
            project=project,
            environment=environment,
        )
    finally:
        _compose(
            project,
            environment,
            "down",
            "--timeout",
            "5",
            "--volumes",
            "--remove-orphans",
            check=False,
            timeout=120,
        )


def test_workspace_data_isolation(demo_stack: _ComposeStack) -> None:
    """Index distinct facts, query both ways, then inspect every storage boundary."""
    with httpx.Client(base_url=demo_stack.api_url, timeout=240) as client:
        workspace_a = _create_workspace(client, "A")
        workspace_b = _create_workspace(client, "B")
        document_a = _upload(client, workspace_a, FILENAME_A, FACT_A)
        document_b = _upload(client, workspace_b, FILENAME_B, FACT_B)
        _wait_until_ready(client, workspace_a, document_a)
        _wait_until_ready(client, workspace_b, document_b)

        result_a = _query(client, workspace_a, f"Where is {MARKER_A} located?")
        result_b = _query(client, workspace_b, f"Where is {MARKER_B} located?")

    _assert_query_isolated(result_a, FACT_A, FACT_B, document_a, document_b)
    _assert_query_isolated(result_b, FACT_B, FACT_A, document_b, document_a)

    boundaries = _inspect_storage_boundaries(demo_stack, workspace_a, workspace_b)
    storage_a = boundaries["storage_keys"][workspace_a]
    storage_b = boundaries["storage_keys"][workspace_b]
    assert storage_a != storage_b

    assert boundaries["postgres_documents"] == {
        workspace_a: [document_a],
        workspace_b: [document_b],
    }
    _assert_marker_partition(
        boundaries["lightrag"], storage_a, storage_b, MARKER_A, MARKER_B
    )
    _assert_marker_partition(
        boundaries["qdrant"], storage_a, storage_b, MARKER_A, MARKER_B
    )
    _assert_marker_partition(
        boundaries["neo4j"], storage_a, storage_b, MARKER_A, MARKER_B
    )


def _create_workspace(client: httpx.Client, suffix: str) -> str:
    response = client.post(
        "/workspaces",
        json={
            "name": f"F039 Workspace {suffix}",
            "slug": f"f039-{suffix.lower()}-{os.getpid()}",
        },
    )
    response.raise_for_status()
    return str(response.json()["id"])


def _upload(client: httpx.Client, workspace_id: str, filename: str, fact: str) -> str:
    response = client.post(
        f"/v1/workspaces/{workspace_id}/documents",
        files={"file": (filename, fact, "text/plain")},
    )
    response.raise_for_status()
    return str(response.json()["id"])


def _query(client: httpx.Client, workspace_id: str, text: str) -> dict[str, Any]:
    response = client.post(f"/v1/workspaces/{workspace_id}/query", json={"query": text})
    response.raise_for_status()
    result: dict[str, Any] = response.json()
    return result


def _assert_query_isolated(
    result: dict[str, Any],
    own_fact: str,
    foreign_fact: str,
    own_document: str,
    foreign_document: str,
) -> None:
    assert own_fact.lower() in result["answer"].lower()
    assert foreign_fact.lower() not in result["answer"].lower()
    source_ids = {str(source["document_id"]) for source in result["sources"]}
    assert own_document in source_ids
    assert foreign_document not in source_ids


def _wait_until_ready(
    client: httpx.Client, workspace_id: str, document_id: str
) -> None:
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        response = client.get(f"/v1/workspaces/{workspace_id}/documents/{document_id}")
        response.raise_for_status()
        status = response.json()["status"]
        if status == "READY":
            return
        if status == "FAILED":
            pytest.fail(f"document {document_id} indexing entered FAILED state")
        time.sleep(0.25)
    pytest.fail(f"document {document_id} did not reach READY state")


def _assert_marker_partition(
    boundary: dict[str, str],
    storage_a: str,
    storage_b: str,
    marker_a: str,
    marker_b: str,
) -> None:
    assert marker_a.lower() in boundary[storage_a].lower()
    assert marker_b.lower() not in boundary[storage_a].lower()
    assert marker_b.lower() in boundary[storage_b].lower()
    assert marker_a.lower() not in boundary[storage_b].lower()


def _inspect_storage_boundaries(
    stack: _ComposeStack, workspace_a: str, workspace_b: str
) -> dict[str, Any]:
    result = _compose(
        stack.project,
        stack.environment,
        "exec",
        "-T",
        "rag-api",
        "/app/.venv/bin/python",
        "-c",
        _INSPECT_BOUNDARIES,
        workspace_a,
        workspace_b,
        timeout=90,
    )
    line = next(
        line
        for line in reversed(result.stdout.splitlines())
        if line.startswith("F039:")
    )
    boundaries: dict[str, Any] = json.loads(line.removeprefix("F039:"))
    return boundaries


_CREATE_BUCKET = """
import boto3
from botocore.config import Config
from backend.config import ApplicationSettings

settings = ApplicationSettings().minio
client = boto3.client(
    "s3",
    endpoint_url=str(settings.endpoint_url).rstrip("/"),
    region_name=settings.region,
    aws_access_key_id=settings.access_key.get_secret_value(),
    aws_secret_access_key=settings.secret_key.get_secret_value(),
    config=Config(s3={"addressing_style": "path"}),
)
client.create_bucket(Bucket=settings.bucket)
"""


_INSPECT_BOUNDARIES = r"""
import asyncio
import json
import sys

import psycopg
from neo4j import GraphDatabase
from qdrant_client import QdrantClient, models

from backend.config import ApplicationSettings


async def inspect():
    workspace_ids = sys.argv[1:3]
    settings = ApplicationSettings()
    password = settings.postgres.password.get_secret_value()
    connection = await psycopg.AsyncConnection.connect(
        host=settings.postgres.host,
        port=settings.postgres.port,
        dbname=settings.postgres.database,
        user=settings.postgres.username,
        password=password,
    )
    async with connection:
        cursor = await connection.execute(
            "SELECT id::text, storage_key FROM graph_blizz.workspaces "
            "WHERE id = ANY(%s::uuid[]) ORDER BY id",
            (workspace_ids,),
        )
        storage_keys = dict(await cursor.fetchall())
        cursor = await connection.execute(
            "SELECT workspace_id::text, id::text FROM graph_blizz.documents "
            "WHERE workspace_id = ANY(%s::uuid[]) ORDER BY workspace_id, id",
            (workspace_ids,),
        )
        postgres_documents = {workspace: [] for workspace in workspace_ids}
        for workspace, document in await cursor.fetchall():
            postgres_documents[workspace].append(document)

        lightrag = {}
        for storage_key in storage_keys.values():
            cursor = await connection.execute(
                "SELECT content FROM lightrag_doc_full WHERE workspace = %s ORDER BY id",
                (storage_key,),
            )
            lightrag[storage_key] = "\n".join(
                str(row[0]) for row in await cursor.fetchall()
            )

    qdrant_client = QdrantClient(url=str(settings.qdrant.url))
    qdrant = {storage_key: [] for storage_key in storage_keys.values()}
    for collection in qdrant_client.get_collections().collections:
        for storage_key in storage_keys.values():
            points, _ = qdrant_client.scroll(
                collection_name=collection.name,
                scroll_filter=models.Filter(
                    must=[models.FieldCondition(
                        key="workspace_id",
                        match=models.MatchValue(value=storage_key),
                    )]
                ),
                limit=1000,
                with_payload=True,
                with_vectors=False,
            )
            qdrant[storage_key].extend(point.payload or {} for point in points)
    qdrant_text = {
        storage_key: json.dumps(payloads, sort_keys=True)
        for storage_key, payloads in qdrant.items()
    }

    neo4j = {}
    driver = GraphDatabase.driver(
        str(settings.neo4j.uri),
        auth=(settings.neo4j.username, settings.neo4j.password.get_secret_value()),
    )
    with driver:
        for storage_key in storage_keys.values():
            records, _, _ = driver.execute_query(
                "MATCH (n) WHERE $workspace IN labels(n) RETURN properties(n) AS data",
                workspace=storage_key,
                database_=settings.neo4j.database,
            )
            neo4j[storage_key] = json.dumps(
                [record["data"] for record in records], sort_keys=True
            )

    return {
        "storage_keys": storage_keys,
        "postgres_documents": postgres_documents,
        "lightrag": lightrag,
        "qdrant": qdrant_text,
        "neo4j": neo4j,
    }


print("F039:" + json.dumps(asyncio.run(inspect()), sort_keys=True))
"""


def _compose(
    project: str,
    environment: dict[str, str],
    *arguments: str,
    check: bool = True,
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    """Run Compose and include its diagnostics in assertion failures."""
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
