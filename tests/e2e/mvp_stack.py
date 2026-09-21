"""Shared full-stack harness for the MVP lifecycle gates."""

import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
V1_FACT = "The Atlas beacon uses the obsolete amber signal."
V2_FACT = "The Atlas beacon uses the current cobalt signal."
SOURCE_FILENAME = "atlas-beacon.txt"


@dataclass(frozen=True, slots=True)
class ComposeStack:
    """Address and Compose identity for one isolated stack."""

    api_url: str
    project: str
    environment: dict[str, str]


class _ModelHandler(BaseHTTPRequestHandler):
    """Serve deterministic OpenAI-compatible embedding and chat responses."""

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
                content = json.dumps(_extraction(prompt))
            elif V1_FACT in prompt:
                content = V1_FACT
            elif V2_FACT in prompt:
                content = V2_FACT
            else:
                content = "The requested fact is not indexed."
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


def require_compose() -> None:
    """Skip only when Docker/Compose infrastructure is unavailable."""
    if shutil.which("docker") is None:
        pytest.skip("Docker CLI is not installed")
    for command in (["docker", "compose", "version"], ["docker", "info"]):
        result = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            pytest.skip(f"{' '.join(command)} is unavailable")


@contextmanager
def isolated_stack(suffix: str) -> Iterator[ComposeStack]:
    """Start a clean full stack backed by persistent named volumes."""
    require_compose()
    server = ThreadingHTTPServer(("0.0.0.0", 0), _ModelHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    project = f"graph_blizz_f042_{suffix}_{os.getpid()}"
    environment = os.environ.copy()
    environment.update(
        {
            "GRAPH_BLIZZ_API_PORT": "0",
            "GRAPH_BLIZZ_MINIO_API_PORT": "0",
            "GRAPH_BLIZZ_MINIO_CONSOLE_PORT": "0",
            "GRAPH_BLIZZ_QDRANT_PORT": "0",
            "GRAPH_BLIZZ_POSTGRES__PASSWORD": "f042-postgres-password",
            "GRAPH_BLIZZ_MINIO__ACCESS_KEY": "f042-access-key",
            "GRAPH_BLIZZ_MINIO__SECRET_KEY": "f042-minio-password",
            "GRAPH_BLIZZ_NEO4J__PASSWORD": "f042-neo4j-password",
            "GRAPH_BLIZZ_EMBEDDING__DIMENSION": "3",
            "GRAPH_BLIZZ_COMPOSE_EMBEDDING_BASE_URL": (
                f"http://host.docker.internal:{server.server_port}/v1"
            ),
            "GRAPH_BLIZZ_COMPOSE_EXTERNAL_LLM_BASE_URL": (
                f"http://host.docker.internal:{server.server_port}/v1"
            ),
        }
    )
    try:
        compose(project, environment, "up", "-d", "--build", "--wait", timeout=420)
        compose(
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
        compose(
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
        yield ComposeStack(
            api_url=resolve_api_url(project, environment),
            project=project,
            environment=environment,
        )
    finally:
        compose(
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
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def compose(
    project: str,
    environment: dict[str, str],
    *arguments: str,
    check: bool = True,
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    """Run Compose and preserve diagnostics for assertion failures."""
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


def resolve_api_url(project: str, environment: dict[str, str]) -> str:
    """Resolve the current ephemeral host port and wait for the public API."""
    published = compose(
        project, environment, "port", "rag-api", "8000", timeout=30
    ).stdout.splitlines()[0]
    api_url = f"http://127.0.0.1:{published.rsplit(':', 1)[1]}"
    deadline = time.monotonic() + 30
    last_error = "no request attempted"
    while time.monotonic() < deadline:
        try:
            response = httpx.get(f"{api_url}/health/ready", timeout=2)
            if response.status_code == 200:
                return api_url
            last_error = f"HTTP {response.status_code}: {response.text}"
        except httpx.HTTPError as error:
            last_error = repr(error)
        time.sleep(0.25)
    pytest.fail(f"API at {api_url} did not become ready: {last_error}")


def inspect_source_of_truth(stack: ComposeStack, document_id: str) -> dict[str, Any]:
    """Read PostgreSQL metadata and immutable originals through stack adapters."""
    result = compose(
        stack.project,
        stack.environment,
        "exec",
        "-T",
        "rag-api",
        "/app/.venv/bin/python",
        "-c",
        _INSPECT_SOURCE_OF_TRUTH,
        document_id,
        timeout=60,
    )
    line = next(
        line
        for line in reversed(result.stdout.splitlines())
        if line.startswith("F042:")
    )
    inspected: dict[str, Any] = json.loads(line.removeprefix("F042:"))
    return inspected


def _embedding(text: str) -> list[float]:
    digest = hashlib.sha256(text.encode()).digest()
    return [digest[index] / 255 for index in range(3)]


def _extraction(prompt: str) -> dict[str, list[dict[str, str]]]:
    fact = V1_FACT if V1_FACT in prompt else V2_FACT
    signal = "amber" if fact == V1_FACT else "cobalt"
    return {
        "entities": [
            {"name": "Atlas beacon", "type": "device", "description": fact},
            {"name": signal, "type": "signal", "description": fact},
        ],
        "relationships": [
            {
                "source": "Atlas beacon",
                "target": signal,
                "keywords": "uses signal",
                "description": fact,
            }
        ],
    }


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


_INSPECT_SOURCE_OF_TRUTH = r"""
import asyncio
import json
import sys

import boto3
import psycopg
from botocore.config import Config

from backend.config import ApplicationSettings


async def inspect(document_id):
    settings = ApplicationSettings()
    connection = await psycopg.AsyncConnection.connect(
        host=settings.postgres.host,
        port=settings.postgres.port,
        dbname=settings.postgres.database,
        user=settings.postgres.username,
        password=settings.postgres.password.get_secret_value(),
    )
    async with connection:
        cursor = await connection.execute(
            "SELECT status, active_revision FROM graph_blizz.documents WHERE id = %s",
            (document_id,),
        )
        document = await cursor.fetchone()
        cursor = await connection.execute(
            "SELECT revision, object_uri FROM graph_blizz.document_revisions "
            "WHERE document_id = %s ORDER BY revision",
            (document_id,),
        )
        revisions = await cursor.fetchall()
        cursor = await connection.execute(
            "SELECT job_type, document_revision, status FROM graph_blizz.jobs "
            "WHERE document_id = %s ORDER BY created_at",
            (document_id,),
        )
        jobs = await cursor.fetchall()

    minio = settings.minio
    client = boto3.client(
        "s3",
        endpoint_url=str(minio.endpoint_url).rstrip("/"),
        region_name=minio.region,
        aws_access_key_id=minio.access_key.get_secret_value(),
        aws_secret_access_key=minio.secret_key.get_secret_value(),
        config=Config(s3={"addressing_style": "path"}),
    )
    sources = []
    for revision, uri in revisions:
        prefix = f"s3://{minio.bucket}/"
        assert uri.startswith(prefix)
        body = client.get_object(Bucket=minio.bucket, Key=uri.removeprefix(prefix))["Body"]
        sources.append([revision, body.read().decode()])
    return {
        "document": list(document),
        "revisions": [[revision, uri] for revision, uri in revisions],
        "jobs": [list(job) for job in jobs],
        "sources": sources,
    }


print("F042:" + json.dumps(asyncio.run(inspect(sys.argv[1])), sort_keys=True))
"""
