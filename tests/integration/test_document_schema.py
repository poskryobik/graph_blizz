"""Integration coverage for persistent document schema invariants."""

import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration
PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def postgres_project() -> Iterator[tuple[str, dict[str, str]]]:
    """Start isolated services and migrate PostgreSQL to the current head."""
    if shutil.which("docker") is None:
        pytest.skip("Docker CLI is not installed")
    for command in (["docker", "compose", "version"], ["docker", "info"]):
        if subprocess.run(command, capture_output=True, check=False).returncode != 0:
            pytest.skip("Docker Compose or daemon is unavailable")

    project = f"graph_blizz_f012_{os.getpid()}"
    environment = os.environ.copy()
    environment["GRAPH_BLIZZ_API_PORT"] = "0"
    environment["GRAPH_BLIZZ_POSTGRES__PASSWORD"] = "f012-integration-password"
    try:
        _compose(project, environment, "up", "-d", "--build", "--wait", timeout=300)
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


def test_document_defaults_and_workspace_local_source_identity(
    postgres_project: tuple[str, dict[str, str]],
) -> None:
    """Create metadata with defaults and reject a duplicate workspace source."""
    project, environment = postgres_project
    workspace_id = _psql(
        project,
        environment,
        "INSERT INTO graph_blizz.workspaces (name, slug) "
        "VALUES ('Knowledge', 'knowledge') RETURNING id;",
    )
    created = _psql(
        project,
        environment,
        "INSERT INTO graph_blizz.documents "
        "(workspace_id, source_key, filename, source_type, object_uri, content_hash) "
        f"VALUES ('{workspace_id}', 'source', 'source.pdf', 'application/pdf', "
        "'s3://documents/source', 'abc') RETURNING status || '|' || "
        "(created_at IS NOT NULL) || '|' || (updated_at IS NOT NULL);",
    )
    assert created == "UPLOADED|true|true"

    duplicate = _compose(
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
        "-v",
        "ON_ERROR_STOP=1",
        "-c",
        "INSERT INTO graph_blizz.documents "
        "(workspace_id, source_key, filename, source_type, object_uri, content_hash) "
        f"VALUES ('{workspace_id}', 'source', 'copy.pdf', 'application/pdf', "
        "'s3://documents/copy', 'def');",
        check=False,
        timeout=30,
    )
    assert duplicate.returncode != 0


def test_document_status_and_workspace_foreign_key_are_enforced(
    postgres_project: tuple[str, dict[str, str]],
) -> None:
    """Keep statuses strict and reject metadata for an unknown workspace."""
    project, environment = postgres_project
    document_id = _psql(
        project,
        environment,
        "SELECT id FROM graph_blizz.documents LIMIT 1;",
    )
    for status in ("UPLOADED", "INDEXING", "READY", "FAILED"):
        stored_status = _psql(
            project,
            environment,
            "UPDATE graph_blizz.documents "
            f"SET status = '{status}' WHERE id = '{document_id}' RETURNING status;",
        )
        assert stored_status == status

    invalid_status = _psql_error(
        project,
        environment,
        "UPDATE graph_blizz.documents SET status = 'UNKNOWN';",
    )
    assert "ck_documents_status" in invalid_status

    invalid_workspace = _psql_error(
        project,
        environment,
        "INSERT INTO graph_blizz.documents "
        "(workspace_id, source_key, filename, source_type, object_uri, content_hash) "
        "VALUES ('00000000-0000-0000-0000-000000000000', 'missing', 'missing', "
        "'text/plain', 's3://documents/missing', 'abc');",
    )
    assert "fk_documents_workspace_id_workspaces" in invalid_workspace


def _psql_error(project: str, environment: dict[str, str], statement: str) -> str:
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
        "-v",
        "ON_ERROR_STOP=1",
        "-c",
        statement,
        check=False,
        timeout=30,
    )
    assert result.returncode != 0
    return result.stderr


def _psql(project: str, environment: dict[str, str], statement: str) -> str:
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
