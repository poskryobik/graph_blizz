"""Integration coverage for Qdrant connectivity and named-volume persistence."""

import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration
PROJECT_ROOT = Path(__file__).resolve().parents[2]
COLLECTION = "f015_persistence_probe"


@pytest.fixture(scope="module")
def compose_project() -> Iterator[tuple[str, dict[str, str]]]:
    """Start an isolated Compose project and remove its volumes afterwards."""
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

    project = f"graph_blizz_f015_{os.getpid()}"
    environment = os.environ.copy()
    environment["GRAPH_BLIZZ_API_PORT"] = "0"
    environment["GRAPH_BLIZZ_MINIO_API_PORT"] = "0"
    environment["GRAPH_BLIZZ_MINIO_CONSOLE_PORT"] = "0"
    environment["GRAPH_BLIZZ_QDRANT_PORT"] = "0"
    environment["GRAPH_BLIZZ_POSTGRES__PASSWORD"] = "f015-postgres-password"
    environment["GRAPH_BLIZZ_MINIO__ACCESS_KEY"] = "f015-access-key"
    environment["GRAPH_BLIZZ_MINIO__SECRET_KEY"] = "f015-minio-password"

    try:
        _compose(project, environment, "up", "-d", "--build", "--wait", timeout=300)
        yield project, environment
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


def test_qdrant_adapter_connects_and_collection_survives_recreate(
    compose_project: tuple[str, dict[str, str]],
) -> None:
    """Prove adapter connectivity and named-volume persistence with real Qdrant."""
    project, environment = compose_project
    _python(
        project,
        environment,
        "from backend.config import ApplicationSettings; "
        "from backend.storage import QdrantConnectivity; "
        "from qdrant_client import QdrantClient,models; "
        "settings=ApplicationSettings().qdrant; "
        "connectivity=QdrantConnectivity(settings); "
        "assert connectivity.check(); connectivity.close(); "
        "client=QdrantClient(url=str(settings.url),timeout=settings.timeout_seconds); "
        f"client.create_collection({COLLECTION!r}, vectors_config=models.VectorParams("
        "size=2,distance=models.Distance.COSINE)); client.close()",
    )

    _compose(project, environment, "stop", "qdrant", timeout=60)
    _compose(
        project,
        environment,
        "up",
        "-d",
        "--force-recreate",
        "--wait",
        "qdrant",
        timeout=120,
    )

    _python(
        project,
        environment,
        "from backend.config import ApplicationSettings; "
        "from qdrant_client import QdrantClient; "
        "settings=ApplicationSettings().qdrant; "
        "client=QdrantClient(url=str(settings.url),timeout=settings.timeout_seconds); "
        f"assert client.collection_exists({COLLECTION!r}); "
        f"client.delete_collection({COLLECTION!r}); client.close()",
    )


def _python(project: str, environment: dict[str, str], script: str) -> None:
    """Execute Python with the deployed adapter inside the application container."""
    _compose(
        project,
        environment,
        "exec",
        "-T",
        "rag-api",
        "/app/.venv/bin/python",
        "-c",
        script,
        timeout=30,
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
