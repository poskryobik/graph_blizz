"""Durable failure recovery for document mutation jobs."""

import asyncio
from collections.abc import Iterator
from datetime import timedelta

import pytest

from backend.config import PostgreSQLSettings
from backend.documents import DocumentRepository, DocumentStatus
from backend.jobs import JobErrorCode, JobRepository, JobStatus
from backend.worker.service import PostgreSQLJobStore, connect_postgres
from tests.integration.worker_db import live_worker_postgres

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def worker_postgres() -> Iterator[PostgreSQLSettings]:
    yield from live_worker_postgres()


def test_retry_terminal_failure_and_stale_attempt_preserve_source_metadata(
    worker_postgres: PostgreSQLSettings,
) -> None:
    async def scenario() -> None:
        async with await connect_postgres(worker_postgres) as connection:
            cursor = await connection.execute(
                "WITH w AS (INSERT INTO graph_blizz.workspaces (name, slug) "
                "VALUES ('Failure recovery', 'failure-recovery') RETURNING id), "
                "d AS (INSERT INTO graph_blizz.documents (workspace_id, source_key, "
                "filename, source_type, status, active_revision) SELECT id, 'stable-source', "
                "'source.txt', 'text/plain', 'FAILED', NULL FROM w "
                "RETURNING id, workspace_id), "
                "r AS (INSERT INTO graph_blizz.document_revisions (document_id, revision, "
                "object_uri, content_hash) SELECT id, 1, "
                "'s3://graph-blizz/stable-source/1', 'immutable-hash' FROM d), "
                "j AS (INSERT INTO graph_blizz.jobs (document_id, document_revision, "
                "status, attempts, max_attempts, lease_owner, lease_expires_at, "
                "heartbeat_at, started_at, updated_at) SELECT id, 1, 'RUNNING', 1, 2, "
                "'stable-worker', now() + interval '5 minutes', now(), now(), now() "
                "FROM d RETURNING id) SELECT d.id, d.workspace_id, j.id FROM d CROSS JOIN j"
            )
            row = await cursor.fetchone()
            assert row is not None
            document_id, workspace_id, job_id = row
            await connection.commit()

        store = PostgreSQLJobStore(worker_postgres)
        retry = await store.fail(
            job_id=job_id,
            owner="stable-worker",
            attempt=1,
            error_code=JobErrorCode.INDEXING_FAILED,
            retry_after=timedelta(0),
        )
        assert retry is not None and retry.status is JobStatus.RETRY
        claimed = await store.claim_next(
            owner="stable-worker", lease_for=timedelta(minutes=5)
        )
        assert claimed is not None and claimed.attempts == 2

        stale = await store.fail(
            job_id=job_id,
            owner="stable-worker",
            attempt=1,
            error_code=JobErrorCode.INDEXING_FAILED,
            retry_after=timedelta(0),
        )
        assert stale is None
        terminal = await store.fail(
            job_id=job_id,
            owner="stable-worker",
            attempt=2,
            error_code=JobErrorCode.INDEXING_FAILED,
            retry_after=timedelta(0),
        )
        assert terminal is not None and terminal.status is JobStatus.FAILED
        assert terminal.error_code is JobErrorCode.ATTEMPTS_EXHAUSTED

        async with await connect_postgres(worker_postgres) as connection:
            document = await DocumentRepository(connection).get_revision(
                workspace_id, document_id, 1
            )
            job = await JobRepository(connection).get(job_id)
        assert document is not None
        assert document.status is DocumentStatus.FAILED
        assert document.active_revision is None
        assert document.source_key == "stable-source"
        assert document.object_uri == "s3://graph-blizz/stable-source/1"
        assert document.content_hash == "immutable-hash"
        assert job is not None and job.status is JobStatus.FAILED
        assert job.document_id == document_id and job.document_revision == 1

    asyncio.run(scenario())


def test_update_and_delete_failures_keep_active_revision_and_lifecycle(
    worker_postgres: PostgreSQLSettings,
) -> None:
    async def scenario() -> None:
        async with await connect_postgres(worker_postgres) as connection:
            cursor = await connection.execute(
                "WITH w AS (INSERT INTO graph_blizz.workspaces (name, slug) "
                "VALUES ('Mutation failures', 'mutation-failures') RETURNING id), "
                "updated AS (INSERT INTO graph_blizz.documents (workspace_id, "
                "source_key, filename, source_type, status, active_revision) "
                "SELECT id, 'update-source', 'update.txt', 'text/plain', "
                "'UPDATING', 1 FROM w RETURNING id, workspace_id), "
                "deleted AS (INSERT INTO graph_blizz.documents (workspace_id, "
                "source_key, filename, source_type, status, active_revision) "
                "SELECT id, 'delete-source', 'delete.txt', 'text/plain', "
                "'DELETING', 1 FROM w RETURNING id, workspace_id), "
                "revisions AS (INSERT INTO graph_blizz.document_revisions "
                "(document_id, revision, object_uri, content_hash) "
                "SELECT id, 1, 's3://failure/update/1', 'update-old' FROM updated "
                "UNION ALL SELECT id, 2, 's3://failure/update/2', 'update-new' "
                "FROM updated UNION ALL SELECT id, 1, "
                "'s3://failure/delete/1', 'delete-active' FROM deleted), "
                "update_job AS (INSERT INTO graph_blizz.jobs (document_id, "
                "document_revision, status, attempts, max_attempts, lease_owner, "
                "lease_expires_at, heartbeat_at, started_at, updated_at) "
                "SELECT id, 2, 'RUNNING', 1, 1, 'mutation-worker', "
                "now() + interval '5 minutes', now(), now(), now() FROM updated "
                "RETURNING id), delete_job AS (INSERT INTO graph_blizz.jobs "
                "(document_id, document_revision, job_type, status, attempts, "
                "max_attempts, lease_owner, lease_expires_at, heartbeat_at, "
                "started_at, updated_at) SELECT id, 1, 'DELETE_DOCUMENT', "
                "'RUNNING', 1, 1, 'mutation-worker', "
                "now() + interval '5 minutes', now(), now(), now() FROM deleted "
                "RETURNING id) SELECT updated.id, updated.workspace_id, "
                "update_job.id, deleted.id, delete_job.id FROM updated "
                "CROSS JOIN update_job CROSS JOIN deleted CROSS JOIN delete_job"
            )
            row = await cursor.fetchone()
            assert row is not None
            update_id, workspace_id, update_job_id, delete_id, delete_job_id = row
            await connection.commit()

        store = PostgreSQLJobStore(worker_postgres)
        for job_id in (update_job_id, delete_job_id):
            failed = await store.fail(
                job_id=job_id,
                owner="mutation-worker",
                attempt=1,
                error_code=JobErrorCode.INDEXING_FAILED,
                retry_after=timedelta(0),
            )
            assert failed is not None and failed.status is JobStatus.FAILED

        async with await connect_postgres(worker_postgres) as connection:
            documents = DocumentRepository(connection)
            update = await documents.get_revision(workspace_id, update_id, 2)
            deletion = await documents.get_revision(workspace_id, delete_id, 1)
        assert update is not None
        assert update.status is DocumentStatus.UPDATING
        assert update.active_revision == 1
        assert update.object_uri == "s3://failure/update/2"
        assert deletion is not None
        assert deletion.status is DocumentStatus.DELETING
        assert deletion.active_revision == 1
        assert deletion.object_uri == "s3://failure/delete/1"

    asyncio.run(scenario())
