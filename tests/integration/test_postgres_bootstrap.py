"""Integration coverage for the F006 PostgreSQL Compose bootstrap."""

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
    """Start an isolated Compose project and remove it after persistence checks."""
    if shutil.which("docker") is None:
        pytest.skip("Docker CLI is not installed")

    availability = subprocess.run(
        ["docker", "compose", "version"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if availability.returncode != 0:
        pytest.skip("Docker Compose is unavailable")

    daemon = subprocess.run(
        ["docker", "info"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if daemon.returncode != 0:
        pytest.skip("Docker daemon is unavailable")

    project = f"graph_blizz_f006_{os.getpid()}"
    environment = os.environ.copy()
    environment["GRAPH_BLIZZ_API_PORT"] = "0"
    environment["GRAPH_BLIZZ_POSTGRES__PASSWORD"] = "f006-integration-password"

    try:
        _compose(
            project,
            environment,
            "up",
            "-d",
            "--build",
            "--wait",
            timeout=300,
        )
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


def test_connectivity_readiness_and_persistence_after_recreate(
    compose_project: tuple[str, dict[str, str]],
) -> None:
    """Prove SQL connectivity, readiness semantics, and named-volume persistence."""
    project, environment = compose_project

    _assert_health(project, environment, "/health/live", 200, "healthy")
    _assert_health(project, environment, "/health/ready", 200, "ready")

    _psql(
        project,
        environment,
        "CREATE TABLE f006_persistence_probe (value text PRIMARY KEY); "
        "INSERT INTO f006_persistence_probe VALUES ('survives-recreate');",
    )

    _compose(project, environment, "stop", "postgres", timeout=60)
    _assert_health(project, environment, "/health/live", 200, "healthy")
    _assert_health(project, environment, "/health/ready", 503, "unready")

    _compose(
        project,
        environment,
        "up",
        "-d",
        "--force-recreate",
        "--wait",
        "postgres",
        timeout=120,
    )

    persisted = _psql(
        project,
        environment,
        "SELECT value FROM f006_persistence_probe;",
    )
    assert persisted == "survives-recreate"
    _assert_health(project, environment, "/health/ready", 200, "ready")


def _compose(
    project: str,
    environment: dict[str, str],
    *arguments: str,
    check: bool = True,
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    """Run Docker Compose for the isolated integration-test project."""
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


def _psql(project: str, environment: dict[str, str], statement: str) -> str:
    """Execute SQL inside PostgreSQL without publishing its network port."""
    result = _compose(
        project,
        environment,
        "exec",
        "-T",
        "postgres",
        "psql",
        "-U",
        "graph_blizz",
        "-d",
        "graph_blizz",
        "-Atqc",
        statement,
        timeout=30,
    )
    return result.stdout.strip()


def _assert_health(
    project: str,
    environment: dict[str, str],
    path: str,
    expected_status: int,
    expected_state: str,
) -> None:
    """Call a health endpoint from inside the internal Compose network."""
    script = (
        "import http.client,json; "
        "connection=http.client.HTTPConnection('localhost',8000,timeout=5); "
        f"connection.request('GET','{path}'); "
        "response=connection.getresponse(); "
        "body=json.loads(response.read()); "
        f"assert response.status=={expected_status}, (response.status,body); "
        f"assert body=={{'status':'{expected_state}'}}, body"
    )
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
