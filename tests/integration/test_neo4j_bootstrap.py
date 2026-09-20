"""Integration coverage for Neo4j connectivity and named-volume persistence."""

import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration
PROJECT_ROOT = Path(__file__).resolve().parents[2]
MARKER = "f016-persistence-probe"


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

    project = f"graph_blizz_f016_{os.getpid()}"
    environment = os.environ.copy()
    environment["GRAPH_BLIZZ_API_PORT"] = "0"
    environment["GRAPH_BLIZZ_MINIO_API_PORT"] = "0"
    environment["GRAPH_BLIZZ_MINIO_CONSOLE_PORT"] = "0"
    environment["GRAPH_BLIZZ_QDRANT_PORT"] = "0"
    environment["GRAPH_BLIZZ_POSTGRES__PASSWORD"] = "f016-postgres-password"
    environment["GRAPH_BLIZZ_MINIO__ACCESS_KEY"] = "f016-access-key"
    environment["GRAPH_BLIZZ_MINIO__SECRET_KEY"] = "f016-minio-password"
    environment["GRAPH_BLIZZ_NEO4J__PASSWORD"] = "f016-neo4j-password"

    try:
        _compose(project, environment, "up", "-d", "--build", "--wait", timeout=360)
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


def test_neo4j_adapter_connects_and_node_survives_recreate(
    compose_project: tuple[str, dict[str, str]],
) -> None:
    """Prove adapter connectivity and named-volume persistence with real Neo4j."""
    project, environment = compose_project
    _python(
        project,
        environment,
        "from backend.config import ApplicationSettings; "
        "from backend.storage import Neo4jConnectivity; "
        "from neo4j import GraphDatabase; "
        "settings=ApplicationSettings().neo4j; "
        "connectivity=Neo4jConnectivity(settings); "
        "assert connectivity.check(); connectivity.close(); "
        "driver=GraphDatabase.driver(str(settings.uri),auth=(settings.username,"
        "settings.password.get_secret_value())); "
        f"driver.execute_query('MERGE (probe:F016Probe {{marker: $marker}})', parameters_={{'marker': {MARKER!r}}}, "
        "database_=settings.database); driver.close()",
    )

    _compose(project, environment, "stop", "neo4j", timeout=60)
    _compose(
        project,
        environment,
        "up",
        "-d",
        "--force-recreate",
        "--wait",
        "neo4j",
        timeout=180,
    )

    _python(
        project,
        environment,
        "from backend.config import ApplicationSettings; "
        "from neo4j import GraphDatabase; "
        "settings=ApplicationSettings().neo4j; "
        "driver=GraphDatabase.driver(str(settings.uri),auth=(settings.username,"
        "settings.password.get_secret_value())); "
        f"records,_,_=driver.execute_query('MATCH (probe:F016Probe {{marker: $marker}}) RETURN count(probe) AS count', parameters_={{'marker': {MARKER!r}}}, database_=settings.database); "
        "assert records[0]['count'] == 1; "
        f"driver.execute_query('MATCH (probe:F016Probe {{marker: $marker}}) DELETE probe', parameters_={{'marker': {MARKER!r}}}, database_=settings.database); "
        "driver.close()",
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
