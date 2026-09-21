"""Repository-level contract coverage for index version reconciliation."""

import asyncio
import subprocess
from collections.abc import Iterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from backend.config import EmbeddingSettings, PostgreSQLSettings
from backend.index_versions import embedding_profile_identity
from backend.worker.service import connect_postgres
from backend.workspaces import WorkspaceRepository
from tests.integration.worker_db import live_worker_postgres

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def postgres() -> Iterator[PostgreSQLSettings]:
    """Provide a migrated PostgreSQL or skip when Docker is unavailable."""
    yield from live_worker_postgres()


def test_migration_offline_sql_preserves_canonical_profile_and_downgrade() -> None:
    profile = embedding_profile_identity(EmbeddingSettings())
    upgrade = subprocess.run(
        [".venv/bin/alembic", "upgrade", "20260921_0009", "--sql"],
        capture_output=True,
        check=True,
        text=True,
    ).stdout
    downgrade = subprocess.run(
        [
            ".venv/bin/alembic",
            "downgrade",
            "20260921_0009:20260918_0008",
            "--sql",
        ],
        capture_output=True,
        check=True,
        text=True,
    ).stdout

    assert f"embedding_profile TEXT DEFAULT '{profile}' NOT NULL" in upgrade
    assert '"dimension"NULL' not in upgrade
    assert "DROP COLUMN embedding_profile" in downgrade
    assert "DROP COLUMN index_schema_version" in downgrade


def test_contract_change_marks_only_active_non_deleted_revisions() -> None:
    workspace_id = UUID("12345678-1234-5678-1234-567812345678")
    now = datetime.now(UTC)
    locked = AsyncMock()
    locked.fetchone.return_value = (
        workspace_id,
        "Contract",
        "contract",
        "storage",
        "ACTIVE",
        now,
        now,
        1,
        '{"model":"embedding-v1"}',
    )
    changed = AsyncMock()
    changed.fetchone.return_value = (
        *locked.fetchone.return_value[:7],
        2,
        '{"model":"embedding-v2"}',
    )
    connection = AsyncMock()
    connection.execute.side_effect = [locked, changed]
    repository = WorkspaceRepository(connection)

    result = asyncio.run(
        repository.ensure_index_contract(
            workspace_id,
            index_schema_version=2,
            embedding_profile='{"model":"embedding-v2"}',
        )
    )

    assert result is not None
    assert result.index_schema_version == 2
    lock_statement, lock_parameters = connection.execute.await_args_list[0].args
    assert "FOR UPDATE" in lock_statement
    assert lock_parameters == (workspace_id,)
    statement, parameters = connection.execute.await_args_list[1].args
    assert "r.revision = d.active_revision" in statement
    assert "requires_reindex = TRUE" in statement
    assert "d.status <> 'DELETED'" in statement
    assert parameters == (2, '{"model":"embedding-v2"}', workspace_id)


def test_contract_change_returns_new_contract_and_marks_exact_revisions(
    postgres: PostgreSQLSettings,
) -> None:
    """Observe changed and unchanged reconciliation against real PostgreSQL."""

    async def scenario() -> None:
        async with await connect_postgres(postgres) as connection:
            cursor = await connection.execute(
                "WITH w AS (INSERT INTO graph_blizz.workspaces (name, slug) "
                "VALUES ('Contract', 'contract-returning') RETURNING id), "
                "live AS (INSERT INTO graph_blizz.documents "
                "(workspace_id, source_key, filename, source_type, active_revision) "
                "SELECT id, 'live', 'live.txt', 'text/plain', 2 FROM w RETURNING id), "
                "live_revisions AS (INSERT INTO graph_blizz.document_revisions "
                "(document_id, revision, object_uri, content_hash) VALUES "
                "((SELECT id FROM live), 1, 's3://contract/live/1', 'live-1'), "
                "((SELECT id FROM live), 2, 's3://contract/live/2', 'live-2')), "
                "deleted AS (INSERT INTO graph_blizz.documents "
                "(workspace_id, source_key, filename, source_type, status, "
                "active_revision) SELECT id, 'deleted', 'deleted.txt', 'text/plain', "
                "'DELETED', 1 FROM w RETURNING id), "
                "deleted_revision AS (INSERT INTO graph_blizz.document_revisions "
                "(document_id, revision, object_uri, content_hash) SELECT id, 1, "
                "'s3://contract/deleted/1', 'deleted-1' FROM deleted) "
                "SELECT id FROM w"
            )
            row = await cursor.fetchone()
            assert row is not None
            workspace_id = row[0]
            repository = WorkspaceRepository(connection)
            profile = '{"model":"embedding-v2"}'

            changed = await repository.ensure_index_contract(
                workspace_id,
                index_schema_version=2,
                embedding_profile=profile,
            )
            assert changed is not None
            assert changed.id == workspace_id
            assert changed.storage_key
            assert changed.index_schema_version == 2
            assert changed.embedding_profile == profile

            revisions = await connection.execute(
                "SELECT d.source_key, r.revision, r.requires_reindex "
                "FROM graph_blizz.documents AS d "
                "JOIN graph_blizz.document_revisions AS r ON r.document_id = d.id "
                "WHERE d.workspace_id = %s ORDER BY d.source_key, r.revision",
                (workspace_id,),
            )
            assert await revisions.fetchall() == [
                ("deleted", 1, False),
                ("live", 1, False),
                ("live", 2, True),
            ]

            unchanged = await repository.ensure_index_contract(
                workspace_id,
                index_schema_version=2,
                embedding_profile=profile,
            )
            assert unchanged == changed
            await connection.rollback()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("second_version", "second_profile"),
    [
        (2, '{"model":"embedding-v2"}'),
        (3, '{"model":"embedding-v3"}'),
    ],
)
def test_concurrent_contract_reconciliation_returns_effective_contract(
    postgres: PostgreSQLSettings,
    second_version: int,
    second_profile: str,
) -> None:
    """Recheck the locked row after a concurrent contract update commits."""

    async def scenario() -> None:
        async with await connect_postgres(postgres) as setup:
            cursor = await setup.execute(
                "WITH w AS (INSERT INTO graph_blizz.workspaces (name, slug) "
                "VALUES ('Contract race', %s) RETURNING id), "
                "d AS (INSERT INTO graph_blizz.documents "
                "(workspace_id, source_key, filename, source_type, active_revision) "
                "SELECT id, 'live', 'live.txt', 'text/plain', 1 FROM w RETURNING id), "
                "r AS (INSERT INTO graph_blizz.document_revisions "
                "(document_id, revision, object_uri, content_hash) "
                "SELECT id, 1, 's3://contract-race/live/1', 'race-1' FROM d) "
                "SELECT id FROM w",
                (f"contract-race-{second_version}",),
            )
            row = await cursor.fetchone()
            assert row is not None
            workspace_id = row[0]
            await setup.commit()

        async with (
            await connect_postgres(postgres) as first_connection,
            await connect_postgres(postgres) as second_connection,
            await connect_postgres(postgres) as observer,
        ):
            first = WorkspaceRepository(first_connection)
            second = WorkspaceRepository(second_connection)
            first_result = await first.ensure_index_contract(
                workspace_id,
                index_schema_version=2,
                embedding_profile='{"model":"embedding-v2"}',
            )
            assert first_result is not None

            pid_cursor = await second_connection.execute("SELECT pg_backend_pid()")
            pid_row = await pid_cursor.fetchone()
            assert pid_row is not None
            second_pid = pid_row[0]
            second_task = asyncio.create_task(
                second.ensure_index_contract(
                    workspace_id,
                    index_schema_version=second_version,
                    embedding_profile=second_profile,
                )
            )

            async with asyncio.timeout(5):
                while True:
                    waiting = await observer.execute(
                        "SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s",
                        (second_pid,),
                    )
                    if await waiting.fetchone() == ("Lock",):
                        break
                    await asyncio.sleep(0.01)

            await first_connection.commit()
            second_result = await second_task
            assert second_result is not None
            assert second_result.index_schema_version == second_version
            assert second_result.embedding_profile == second_profile
            await second_connection.commit()

            checked = await observer.execute(
                "SELECT w.index_schema_version, w.embedding_profile, "
                "count(*), count(*) FILTER (WHERE r.revision = 1 "
                "AND r.requires_reindex) "
                "FROM graph_blizz.workspaces AS w "
                "JOIN graph_blizz.documents AS d ON d.workspace_id = w.id "
                "JOIN graph_blizz.document_revisions AS r ON r.document_id = d.id "
                "WHERE w.id = %s GROUP BY w.id",
                (workspace_id,),
            )
            assert await checked.fetchone() == (
                second_version,
                second_profile,
                1,
                1,
            )

    asyncio.run(scenario())
