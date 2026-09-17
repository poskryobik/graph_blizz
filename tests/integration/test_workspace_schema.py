"""Integration coverage for persistent workspace schema invariants."""

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

    project = f"graph_blizz_f008_{os.getpid()}"
    environment = os.environ.copy()
    environment["GRAPH_BLIZZ_API_PORT"] = "0"
    environment["GRAPH_BLIZZ_POSTGRES__PASSWORD"] = "f008-integration-password"
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


def test_workspace_defaults_and_rename_preserve_storage_key(
    postgres_project: tuple[str, dict[str, str]],
) -> None:
    """Generate physical identity in PostgreSQL and keep it across rename."""
    project, environment = postgres_project
    created = _psql(
        project,
        environment,
        "INSERT INTO graph_blizz.workspaces (name, slug) "
        "VALUES ('Knowledge', 'knowledge') "
        "RETURNING id::text || '|' || storage_key || '|' || status || '|' || "
        "(created_at IS NOT NULL) || '|' || (updated_at IS NOT NULL);",
    )
    workspace_id, storage_key, status, created_set, updated_set = created.split("|")
    assert len(workspace_id) == 36
    assert storage_key.startswith("ws_")
    assert status == "ACTIVE"
    assert (created_set, updated_set) == ("true", "true")

    renamed_key = _psql(
        project,
        environment,
        "UPDATE graph_blizz.workspaces "
        "SET name = 'Renamed', slug = 'renamed', updated_at = CURRENT_TIMESTAMP "
        f"WHERE id = '{workspace_id}' RETURNING storage_key;",
    )
    assert renamed_key == storage_key


def test_storage_key_is_immutable_and_schema_has_no_auth_dependencies(
    postgres_project: tuple[str, dict[str, str]],
) -> None:
    """Reject physical identity changes and avoid foreign keys to auth tables."""
    project, environment = postgres_project
    mutation = _compose(
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
        "UPDATE graph_blizz.workspaces SET storage_key = 'client-selected';",
        check=False,
        timeout=30,
    )
    assert mutation.returncode != 0
    assert "immutable" in mutation.stderr

    foreign_keys = _psql(
        project,
        environment,
        "SELECT count(*) FROM information_schema.table_constraints "
        "WHERE table_schema = 'graph_blizz' AND table_name = 'workspaces' "
        "AND constraint_type = 'FOREIGN KEY';",
    )
    assert foreign_keys == "0"


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
