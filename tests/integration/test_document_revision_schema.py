"""Integration coverage for revision migration and database invariants."""

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
    """Start isolated services when Docker is available."""
    if shutil.which("docker") is None:
        pytest.skip("Docker CLI is not installed")
    for command in (["docker", "compose", "version"], ["docker", "info"]):
        if subprocess.run(command, capture_output=True, check=False).returncode != 0:
            pytest.skip("Docker Compose or daemon is unavailable")
    project = f"graph_blizz_f027_{os.getpid()}"
    environment = os.environ.copy()
    environment["GRAPH_BLIZZ_API_PORT"] = "0"
    environment["GRAPH_BLIZZ_POSTGRES__PASSWORD"] = "f027-integration-password"
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


def test_demo_rows_migrate_to_immutable_revision_one(
    postgres_project: tuple[str, dict[str, str]],
) -> None:
    """Preserve the logical id, hash, and URI while normalizing old rows."""
    project, environment = postgres_project
    _upgrade(project, environment, "20260917_0003")
    workspace_id = _psql(
        project,
        environment,
        "INSERT INTO graph_blizz.workspaces (name, slug) "
        "VALUES ('Legacy', 'legacy') RETURNING id;",
    )
    document_id = _psql(
        project,
        environment,
        "INSERT INTO graph_blizz.documents "
        "(workspace_id, source_key, filename, source_type, object_uri, content_hash) "
        f"VALUES ('{workspace_id}', 'legacy', 'legacy.md', 'text/markdown', "
        "'s3://documents/legacy/source', 'legacy-hash') RETURNING id;",
    )

    _upgrade(project, environment, "head")

    migrated = _psql(
        project,
        environment,
        "SELECT d.id || '|' || d.active_revision || '|' || r.revision || '|' || "
        "r.object_uri || '|' || r.content_hash "
        "FROM graph_blizz.documents AS d "
        "JOIN graph_blizz.document_revisions AS r ON r.document_id = d.id "
        f"WHERE d.id = '{document_id}';",
    )
    assert migrated == (f"{document_id}|1|1|s3://documents/legacy/source|legacy-hash")
    error = _psql_error(
        project,
        environment,
        "UPDATE graph_blizz.document_revisions SET content_hash = 'changed' "
        f"WHERE document_id = '{document_id}' AND revision = 1;",
    )
    assert "document revisions are immutable" in error
    invalid_active = _psql_error(
        project,
        environment,
        "UPDATE graph_blizz.documents SET active_revision = 2 "
        f"WHERE id = '{document_id}';",
    )
    assert "fk_documents_active_revision_document_revisions" in invalid_active
    activation_column = _psql(
        project,
        environment,
        "SELECT is_nullable || '|' || COALESCE(column_default, '') "
        "FROM information_schema.columns WHERE table_schema = 'graph_blizz' "
        "AND table_name = 'documents' AND column_name = 'active_revision';",
    )
    assert activation_column == "YES|"


def _upgrade(project: str, environment: dict[str, str], target: str) -> None:
    _compose(
        project,
        environment,
        "exec",
        "-T",
        "rag-api",
        "/app/.venv/bin/alembic",
        "upgrade",
        target,
        timeout=60,
    )


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
