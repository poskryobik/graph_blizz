"""Complete Demo-owner Graph RAG workflow against a clean Compose stack."""

import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest

pytestmark = pytest.mark.e2e
PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXPECTED_FACT = "The Aurora Finch observatory is located on Cedar Island."
SOURCE_FILENAME = "aurora-finch.txt"


class _ModelHandler(BaseHTTPRequestHandler):
    """Provide deterministic OpenAI-compatible models to the Compose API."""

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length))
        if self.path == "/v1/embeddings":
            inputs = payload["input"]
            data = [
                {
                    "index": index,
                    "embedding": _embedding(text),
                }
                for index, text in enumerate(inputs)
            ]
            self._respond({"data": data})
            return
        if self.path == "/v1/chat/completions":
            prompt = "\n".join(
                str(message.get("content", "")) for message in payload["messages"]
            )
            if "identify and extract any missed" in prompt:
                content = '{"entities": [], "relationships": []}'
            elif "Strict Adherence to JSON Format" in prompt and "Input Text" in prompt:
                content = json.dumps(
                    {
                        "entities": [
                            {
                                "name": "Aurora Finch observatory",
                                "type": "place",
                                "description": EXPECTED_FACT,
                            },
                            {
                                "name": "Cedar Island",
                                "type": "place",
                                "description": "The observatory location.",
                            },
                        ],
                        "relationships": [
                            {
                                "source": "Aurora Finch observatory",
                                "target": "Cedar Island",
                                "keywords": "located on",
                                "description": EXPECTED_FACT,
                            }
                        ],
                    }
                )
            else:
                content = EXPECTED_FACT
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
def demo_api(model_server: int) -> Iterator[str]:
    """Start an isolated clean Compose stack and return its public API URL."""
    project = f"graph_blizz_f025_{os.getpid()}"
    environment = os.environ.copy()
    environment.update(
        {
            "GRAPH_BLIZZ_API_PORT": "0",
            "GRAPH_BLIZZ_MINIO_API_PORT": "0",
            "GRAPH_BLIZZ_MINIO_CONSOLE_PORT": "0",
            "GRAPH_BLIZZ_QDRANT_PORT": "0",
            "GRAPH_BLIZZ_POSTGRES__PASSWORD": "f025-postgres-password",
            "GRAPH_BLIZZ_MINIO__ACCESS_KEY": "f025-access-key",
            "GRAPH_BLIZZ_MINIO__SECRET_KEY": "f025-minio-password",
            "GRAPH_BLIZZ_NEO4J__PASSWORD": "f025-neo4j-password",
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
            project,
            environment,
            "port",
            "rag-api",
            "8000",
            timeout=30,
        ).stdout.splitlines()[0]
        yield f"http://127.0.0.1:{published.rsplit(':', 1)[1]}"
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


def test_demo_graph_rag_happy_path(demo_api: str) -> None:
    """Create, index, and query a document without credentials or an IdP."""
    with httpx.Client(base_url=demo_api, timeout=240) as client:
        workspace_response = client.post(
            "/workspaces",
            json={"name": "F025 Demo", "slug": f"f025-demo-{os.getpid()}"},
        )
        workspace_response.raise_for_status()
        workspace_id = workspace_response.json()["id"]

        upload_response = client.post(
            f"/v1/workspaces/{workspace_id}/documents",
            files={"file": (SOURCE_FILENAME, EXPECTED_FACT, "text/plain")},
        )
        upload_response.raise_for_status()
        document_id = upload_response.json()["id"]

        document = _wait_until_ready(client, workspace_id, document_id)
        assert document["filename"] == SOURCE_FILENAME

        query_response = client.post(
            f"/v1/workspaces/{workspace_id}/query",
            json={"query": "Where is the Aurora Finch observatory located?"},
        )
        query_response.raise_for_status()

    result = query_response.json()
    assert EXPECTED_FACT.lower() in result["answer"].lower()
    assert {"document_id": document_id, "filename": SOURCE_FILENAME} in result[
        "sources"
    ]


def _wait_until_ready(
    client: httpx.Client, workspace_id: str, document_id: str
) -> dict[str, Any]:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        response = client.get(f"/v1/workspaces/{workspace_id}/documents/{document_id}")
        response.raise_for_status()
        document: dict[str, Any] = response.json()
        if document["status"] == "READY":
            return document
        if document["status"] == "FAILED":
            pytest.fail("document indexing entered FAILED state")
        time.sleep(0.25)
    pytest.fail("document did not reach READY state")


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
