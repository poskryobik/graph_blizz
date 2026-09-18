"""Integration coverage for versioned application database migrations."""

import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration
PROJECT_ROOT = Path(__file__).resolve().parents[2]
REVISION = "20260918_0007"


@pytest.fixture(scope="module")
def compose_project() -> Iterator[tuple[str, dict[str, str]]]:
    """Start an isolated PostgreSQL and API image containing migration assets."""
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

    project = f"graph_blizz_f007_{os.getpid()}"
    environment = os.environ.copy()
    environment["GRAPH_BLIZZ_API_PORT"] = "0"
    environment["GRAPH_BLIZZ_POSTGRES__PASSWORD"] = "f007-integration-password"

    try:
        _compose(project, environment, "up", "-d", "--build", "--wait", timeout=300)
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


def test_fresh_and_repeated_upgrade_reach_head(
    compose_project: tuple[str, dict[str, str]],
) -> None:
    """Create the app schema at head and prove a second upgrade is a no-op."""
    project, environment = compose_project

    assert (
        _psql(
            project,
            environment,
            "SELECT count(*) FROM information_schema.schemata "
            "WHERE schema_name = 'graph_blizz';",
        )
        == "0"
    )
    _upgrade(project, environment)
    first_snapshot = _schema_snapshot(project, environment)
    assert first_snapshot == f"graph_blizz|alembic_version|{REVISION}"
    document_status_constraint = _psql(
        project,
        environment,
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE connamespace = 'graph_blizz'::regnamespace "
        "AND conname = 'ck_documents_status';",
    )
    assert {"UPDATING", "DELETING", "DELETED"} <= set(
        document_status_constraint.split("'")[1::2]
    )
    assert "DELETE_DOCUMENT" in _psql(
        project,
        environment,
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE connamespace = 'graph_blizz'::regnamespace "
        "AND conname = 'ck_jobs_job_type';",
    )

    _upgrade(project, environment)
    assert _schema_snapshot(project, environment) == first_snapshot


def _upgrade(project: str, environment: dict[str, str]) -> None:
    """Run the bundled Alembic CLI inside the application container."""
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


def _schema_snapshot(project: str, environment: dict[str, str]) -> str:
    """Return application schema, version table, and current revision."""
    statement = (
        "SELECT concat_ws('|', "
        "(SELECT schema_name FROM information_schema.schemata "
        "WHERE schema_name = 'graph_blizz'), "
        "(SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_name = 'alembic_version'), "
        "(SELECT version_num FROM public.alembic_version LIMIT 1));"
    )
    return _psql(project, environment, statement)


def _psql(project: str, environment: dict[str, str], statement: str) -> str:
    """Execute SQL inside the isolated PostgreSQL container."""
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
