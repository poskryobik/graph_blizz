"""Integration contract for retry and expired lease recovery."""

import asyncio
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest

from backend.config import PostgreSQLSettings
from backend.documents import DocumentRepository, DocumentStatus
from backend.jobs import JobStatus
from backend.worker.service import (
    IndexingJobProcessor,
    PostgreSQLJobStore,
    connect_postgres,
)
from tests.integration.worker_db import live_worker_postgres

pytestmark = pytest.mark.integration
REPOSITORY = Path("backend/jobs/repository.py")


@pytest.fixture(scope="module")
def worker_postgres() -> Iterator[PostgreSQLSettings]:
    yield from live_worker_postgres()


def test_expired_running_jobs_are_retried_or_failed_atomically() -> None:
    sql = REPOSITORY.read_text()
    recovery = sql.split("async def recover_expired", 1)[1].split(
        "async def commit", 1
    )[0]

    assert "WHERE status = 'RUNNING' AND lease_expires_at <= now()" in recovery
    assert "attempts < max_attempts THEN 'RETRY' ELSE 'FAILED'" in recovery
    assert "ATTEMPTS_EXHAUSTED" in recovery
    assert "lease_owner = NULL" in recovery
    assert "heartbeat_at = NULL" in recovery


def test_worker_is_a_separate_compose_process_with_grace_period() -> None:
    compose = Path("docker-compose.yml").read_text()

    worker = compose.split("  rag-worker:", 1)[1].split("\nvolumes:", 1)[0]
    assert '"-m", "backend.worker"' in worker
    assert "stop_grace_period:" in worker
    assert "GRAPH_BLIZZ_WORKER__LEASE_SECONDS" in worker


def test_expired_lease_becomes_claimable_without_incrementing_on_recovery(
    worker_postgres: PostgreSQLSettings,
) -> None:
    async def scenario() -> None:
        async with await connect_postgres(worker_postgres) as connection:
            cursor = await connection.execute(
                "WITH w AS (INSERT INTO graph_blizz.workspaces (name, slug) "
                "VALUES ('Recovery', 'recovery') RETURNING id), d AS (INSERT INTO "
                "graph_blizz.documents (workspace_id, source_key, filename, source_type) "
                "SELECT id, 'recovery', 'recovery.txt', 'text/plain' FROM w RETURNING id), "
                "r AS (INSERT INTO graph_blizz.document_revisions "
                "(document_id, revision, object_uri, content_hash) SELECT id, 1, "
                "'s3://graph-blizz/recovery', 'hash' FROM d), j AS (INSERT INTO "
                "graph_blizz.jobs (document_id, document_revision, status, attempts, "
                "lease_owner, lease_expires_at, heartbeat_at, started_at, updated_at) "
                "SELECT id, 1, 'RUNNING', 1, 'dead-worker', now() - interval '1 second', "
                "now() - interval '1 minute', now() - interval '1 minute', now() FROM d "
                "RETURNING id) SELECT id FROM j"
            )
            row = await cursor.fetchone()
            assert row is not None
            job_id = row[0]
            await connection.commit()
        store = PostgreSQLJobStore(worker_postgres)
        recovered = await store.recover_expired(retry_after=timedelta(0))
        assert recovered[0].id == job_id
        assert recovered[0].status is JobStatus.RETRY
        assert recovered[0].attempts == 1
        claimed = await store.claim_next(
            owner="replacement", lease_for=timedelta(minutes=1)
        )
        assert claimed is not None
        assert claimed.id == job_id
        assert claimed.attempts == 2

    asyncio.run(scenario())


def test_replacement_waits_for_old_execution_and_old_cleanup_is_fenced(
    worker_postgres: PostgreSQLSettings,
) -> None:
    """Lease recovery cannot overlap mutation or let stale cleanup win."""

    async def scenario() -> None:
        async with await connect_postgres(worker_postgres) as setup:
            cursor = await setup.execute(
                "WITH w AS (INSERT INTO graph_blizz.workspaces (name, slug) "
                "VALUES ('Fence', 'fence') RETURNING id), d AS (INSERT INTO "
                "graph_blizz.documents (workspace_id, source_key, filename, source_type, "
                "status) SELECT id, 'fence', 'fence.txt', 'text/plain', 'INDEXING' FROM w "
                "RETURNING id, workspace_id), r AS (INSERT INTO "
                "graph_blizz.document_revisions (document_id, revision, object_uri, "
                "content_hash) SELECT id, 1, 's3://graph-blizz/fence', 'hash' FROM d), "
                "j AS (INSERT INTO graph_blizz.jobs (document_id, document_revision, "
                "status, attempts, lease_owner, lease_expires_at, heartbeat_at, "
                "started_at, updated_at) SELECT id, 1, 'RUNNING', 1, 'old-worker', "
                "now() + interval '1 minute', now(), now(), now() FROM d RETURNING id) "
                "SELECT d.id, d.workspace_id, j.id FROM d CROSS JOIN j"
            )
            row = await cursor.fetchone()
            assert row is not None
            document_id, workspace_id, job_id = row
            await setup.commit()

        old = await connect_postgres(worker_postgres)
        replacement = await connect_postgres(worker_postgres)
        lock_key = IndexingJobProcessor._document_lock_key(document_id)
        try:
            await old.execute("SELECT pg_advisory_lock(%s)", (lock_key,))
            await old.commit()
            await old.execute(
                "UPDATE graph_blizz.jobs SET lease_expires_at = now() - interval "
                "'1 second' WHERE id = %s",
                (job_id,),
            )
            await old.commit()
            store = PostgreSQLJobStore(worker_postgres)
            await store.recover_expired(retry_after=timedelta(0))
            claimed = await store.claim_next(
                owner="replacement", lease_for=timedelta(minutes=1)
            )
            assert claimed is not None
            assert claimed.attempts == 2

            waiting = asyncio.create_task(
                replacement.execute("SELECT pg_advisory_lock(%s)", (lock_key,))
            )
            await asyncio.sleep(0.05)
            assert not waiting.done()

            stale = await DocumentRepository(old).transition_status_for_job(
                workspace_id=workspace_id,
                document_id=document_id,
                from_status=DocumentStatus.INDEXING,
                to_status=DocumentStatus.FAILED,
                job_id=job_id,
                lease_owner="old-worker",
                attempt=1,
            )
            assert stale is None
            await old.commit()
            await old.execute("SELECT pg_advisory_unlock(%s)", (lock_key,))
            await waiting

            reset = await DocumentRepository(replacement).transition_status_for_job(
                workspace_id=workspace_id,
                document_id=document_id,
                from_status=DocumentStatus.INDEXING,
                to_status=DocumentStatus.UPLOADED,
                job_id=job_id,
                lease_owner="replacement",
                attempt=2,
            )
            assert reset is not None
            assert reset.status is DocumentStatus.UPLOADED
        finally:
            await old.close()
            await replacement.close()

    asyncio.run(scenario())
