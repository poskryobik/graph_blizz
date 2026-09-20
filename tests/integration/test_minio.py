"""Integration coverage for MinIO persistence and the ObjectStore adapter."""

import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration
PROJECT_ROOT = Path(__file__).resolve().parents[2]
OBJECT_KEY = "f011/persistence-probe.bin"


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

    project = f"graph_blizz_f011_{os.getpid()}"
    environment = os.environ.copy()
    environment["GRAPH_BLIZZ_API_PORT"] = "0"
    environment["GRAPH_BLIZZ_POSTGRES__PASSWORD"] = "f011-postgres-password"
    environment["GRAPH_BLIZZ_MINIO__ACCESS_KEY"] = "f011-access-key"
    environment["GRAPH_BLIZZ_MINIO__SECRET_KEY"] = "f011-minio-password"

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


def test_object_survives_minio_recreate_then_can_be_read_and_deleted(
    compose_project: tuple[str, dict[str, str]],
) -> None:
    """Prove named-volume persistence and adapter get/delete against real MinIO."""
    project, environment = compose_project
    _python(
        project,
        environment,
        "import boto3; "
        "from botocore.config import Config; "
        "from backend.config import ApplicationSettings; "
        "from backend.storage import ObjectStore; "
        "settings=ApplicationSettings().minio; "
        "client=boto3.client('s3', endpoint_url=str(settings.endpoint_url), "
        "region_name=settings.region, "
        "aws_access_key_id=settings.access_key.get_secret_value(), "
        "aws_secret_access_key=settings.secret_key.get_secret_value(), "
        "config=Config(s3={'addressing_style':'path'})); "
        "client.create_bucket(Bucket=settings.bucket); "
        "store=ObjectStore(settings, client=client); "
        f"store.put({OBJECT_KEY!r}, b'survives-recreate')",
    )

    _compose(project, environment, "stop", "minio", timeout=60)
    _compose(
        project,
        environment,
        "up",
        "-d",
        "--force-recreate",
        "--wait",
        "minio",
        timeout=120,
    )

    _python(
        project,
        environment,
        "from backend.config import ApplicationSettings; "
        "from backend.storage import ObjectNotFoundError,ObjectStore; "
        "store=ObjectStore(ApplicationSettings().minio); "
        f"assert store.get({OBJECT_KEY!r}) == b'survives-recreate'; "
        f"store.delete({OBJECT_KEY!r}); "
        "missing=False; "
        "\ntry: store.get(" + repr(OBJECT_KEY) + ")\n"
        "except ObjectNotFoundError: missing=True\n"
        "assert missing",
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
