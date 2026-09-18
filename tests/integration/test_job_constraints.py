"""Integration coverage for durable job PostgreSQL constraints."""

import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration
MIGRATION = Path("migrations/versions/20260918_0005_create_durable_jobs.py")
PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def postgres_project() -> Iterator[tuple[str, dict[str, str]]]:
    """Start an isolated migrated PostgreSQL when Docker is available."""
    if shutil.which("docker") is None:
        pytest.skip("Docker CLI is not installed")
    for command in (["docker", "compose", "version"], ["docker", "info"]):
        if subprocess.run(command, capture_output=True, check=False).returncode != 0:
            pytest.skip("Docker Compose or daemon is unavailable")
    project = f"graph_blizz_f028_{os.getpid()}"
    environment = os.environ.copy()
    environment["GRAPH_BLIZZ_API_PORT"] = "0"
    environment["GRAPH_BLIZZ_POSTGRES__PASSWORD"] = "f028-integration-password"
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


def test_migration_persists_job_lifecycle_and_revision_reference() -> None:
    sql = MIGRATION.read_text()

    for status in ("PENDING", "RUNNING", "RETRY", "SUCCEEDED", "FAILED", "CANCELLED"):
        assert status in sql
    assert "fk_jobs_document_revision" in sql
    assert "lease_expires_at" in sql
    assert "heartbeat_at" in sql
    assert "ck_jobs_attempts" in sql
    assert "ck_jobs_error_detail" in sql
    assert '"error_detail IS NULL"' in sql
    assert "API_KEY_SUPERSECRET" not in sql
    assert "Traceback" not in sql
    assert "ck_jobs_attempts_started_pair" in sql
    assert "ck_jobs_updated_order" in sql
    assert "ck_jobs_updated_after_finished" in sql


def test_partial_unique_index_only_conflicts_for_active_mutations() -> None:
    sql = MIGRATION.read_text()

    assert "uq_jobs_active_document_mutation" in sql
    assert '["document_id"]' in sql
    assert "unique=True" in sql
    assert "status IN ('PENDING', 'RUNNING', 'RETRY')" in sql
    index_clause = sql.split("uq_jobs_active_document_mutation", 1)[1]
    assert "SUCCEEDED" not in index_clause
    assert "FAILED" not in index_clause
    assert "CANCELLED" not in index_clause


def test_database_rejects_conflicting_active_job_but_allows_after_terminal(
    postgres_project: tuple[str, dict[str, str]],
) -> None:
    """Enforce one active mutation while allowing any number of terminal jobs."""
    project, environment = postgres_project
    document_id = _psql(
        project,
        environment,
        "WITH w AS (INSERT INTO graph_blizz.workspaces (name, slug) "
        "VALUES ('Jobs', 'jobs') RETURNING id), d AS (INSERT INTO "
        "graph_blizz.documents (workspace_id, source_key, filename, source_type) "
        "SELECT id, 'guide', 'guide.md', 'text/markdown' FROM w RETURNING id), "
        "r AS (INSERT INTO graph_blizz.document_revisions "
        "(document_id, revision, object_uri, content_hash) SELECT id, 1, "
        "'s3://jobs/guide/1', 'hash' FROM d) SELECT id FROM d;",
    )
    _psql(
        project,
        environment,
        "INSERT INTO graph_blizz.jobs (document_id, document_revision) "
        f"VALUES ('{document_id}', 1);",
    )
    conflict = _psql_error(
        project,
        environment,
        "INSERT INTO graph_blizz.jobs (document_id, document_revision) "
        f"VALUES ('{document_id}', 1);",
    )
    assert "uq_jobs_active_document_mutation" in conflict
    invalid_updates = (
        ("error_detail = 'Traceback: ValueError secret-token'", "ck_jobs_error_detail"),
        ("error_detail = 'api_key=supersecret'", "ck_jobs_error_detail"),
        (
            (
                "status = 'CANCELLED', attempts = 1, started_at = NULL, "
                "finished_at = created_at"
            ),
            "ck_jobs_attempts_started_pair",
        ),
        (
            (
                "status = 'CANCELLED', attempts = 0, started_at = created_at, "
                "finished_at = created_at"
            ),
            "ck_jobs_attempts_started_pair",
        ),
        ("available_at = created_at - interval '1 second'", "ck_jobs_timestamp_order"),
        ("updated_at = created_at - interval '1 second'", "ck_jobs_timestamp_order"),
        (
            (
                "status = 'CANCELLED', attempts = 1, "
                "started_at = created_at - interval '1 second', "
                "finished_at = created_at"
            ),
            "ck_jobs_started_order",
        ),
        (
            (
                "status = 'RETRY', attempts = 1, "
                "started_at = created_at + interval '1 second', "
                "finished_at = NULL"
            ),
            "ck_jobs_updated_order",
        ),
        (
            (
                "status = 'RUNNING', attempts = 1, lease_owner = 'worker', "
                "started_at = created_at + interval '1 second', "
                "heartbeat_at = created_at, "
                "lease_expires_at = created_at + interval '2 seconds', "
                "updated_at = created_at + interval '2 seconds'"
            ),
            "ck_jobs_heartbeat_order",
        ),
        (
            (
                "status = 'RUNNING', attempts = 1, lease_owner = 'worker', "
                "started_at = created_at, "
                "heartbeat_at = created_at + interval '1 second', "
                "lease_expires_at = created_at + interval '2 seconds'"
            ),
            "ck_jobs_updated_order",
        ),
        (
            (
                "status = 'RUNNING', attempts = 1, lease_owner = '   ', "
                "started_at = created_at, heartbeat_at = created_at, "
                "lease_expires_at = created_at + interval '1 second'"
            ),
            "ck_jobs_lease",
        ),
        (
            (
                "status = 'CANCELLED', attempts = 1, "
                "started_at = created_at + interval '1 second', "
                "finished_at = created_at, "
                "updated_at = created_at + interval '2 seconds'"
            ),
            "ck_jobs_finished_order",
        ),
        (
            "status = 'CANCELLED', finished_at = created_at + interval '1 second'",
            "ck_jobs_updated_after_finished",
        ),
    )
    for assignments, constraint in invalid_updates:
        error = _psql_error(
            project,
            environment,
            f"UPDATE graph_blizz.jobs SET {assignments} "
            f"WHERE document_id = '{document_id}';",
        )
        assert constraint in error
    _psql(
        project,
        environment,
        "UPDATE graph_blizz.jobs SET status = 'CANCELLED', finished_at = now() "
        f"WHERE document_id = '{document_id}';",
    )
    _psql(
        project,
        environment,
        "INSERT INTO graph_blizz.jobs (document_id, document_revision) "
        f"VALUES ('{document_id}', 1);",
    )
    _psql(
        project,
        environment,
        "UPDATE graph_blizz.jobs SET status = 'RUNNING', attempts = 1, "
        "started_at = now(), heartbeat_at = now(), updated_at = now(), "
        "lease_expires_at = now() + interval '1 minute', lease_owner = 'worker-1' "
        f"WHERE document_id = '{document_id}' AND status = 'PENDING';",
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
        pytest.fail(f"docker compose failed:\n{result.stdout}\n{result.stderr}")
    return result
