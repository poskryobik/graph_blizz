"""Live PostgreSQL coverage for durable document deletion."""

import asyncio
from collections.abc import Iterator
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import psycopg
import pytest

from backend.config import PostgreSQLSettings
from backend.documents import DocumentRepository, DocumentSourceService, DocumentStatus
from backend.jobs import JobRepository, JobStatus, JobType
from backend.parsers import ParsedDocument
from backend.worker import service as worker_service
from backend.worker.service import IndexingJobProcessor, LeasedDocumentRepository
from tests.integration.worker_db import live_worker_postgres

pytestmark = pytest.mark.integration
WORKSPACE_ID = UUID("12345678-1234-5678-1234-567812345678")
DOCUMENT_ID = UUID("eeeeeeee-1234-5678-1234-567812345678")


@pytest.fixture(scope="module")
def document_postgres() -> Iterator[PostgreSQLSettings]:
    yield from live_worker_postgres()


def test_public_lifecycle_pending_claim_process_retains_source(
    document_postgres: PostgreSQLSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        async with await _connect(document_postgres) as connection:
            await connection.execute(
                "INSERT INTO graph_blizz.workspaces (id, name, slug) "
                "VALUES (%s, 'F033', 'f033')",
                (WORKSPACE_ID,),
            )
            documents = DocumentRepository(connection)
            await documents.create(
                document_id=DOCUMENT_ID,
                workspace_id=WORKSPACE_ID,
                source_key="stable",
                filename="notes.md",
                source_type="text/markdown",
                object_uri="s3://bucket/revision/1",
                content_hash="hash",
            )
            await documents.transition_status(
                workspace_id=WORKSPACE_ID,
                document_id=DOCUMENT_ID,
                from_status=DocumentStatus.UPLOADED,
                to_status=DocumentStatus.READY,
            )
            await documents.commit()

        async with await _connect(document_postgres) as connection:
            documents = DocumentRepository(connection)
            jobs = JobRepository(connection)
            result = await DocumentSourceService(
                documents, MagicMock()
            ).delete_for_indexing(
                jobs=jobs, workspace_id=WORKSPACE_ID, document_id=DOCUMENT_ID
            )
            assert result.document.status is DocumentStatus.DELETING
            assert result.job is not None
            assert result.job.status is JobStatus.PENDING
            assert result.job.type is JobType.DELETE_DOCUMENT
            pending_id = result.job.id

            repeated = await DocumentSourceService(
                documents, MagicMock()
            ).delete_for_indexing(
                jobs=jobs, workspace_id=WORKSPACE_ID, document_id=DOCUMENT_ID
            )
            assert repeated.job is not None
            assert repeated.job.id == pending_id

            claimed = await jobs.claim_next(
                owner="worker-1", lease_for=timedelta(minutes=5)
            )
            await jobs.commit()
            assert claimed is not None
            assert claimed.id == pending_id
            assert claimed.status is JobStatus.RUNNING

        async with await _connect(document_postgres) as connection:
            fenced = LeasedDocumentRepository(connection, claimed)
            assert (
                await fenced.complete_deletion_for_job(
                    workspace_id=WORKSPACE_ID,
                    document_id=DOCUMENT_ID,
                    revision=1,
                    job_id=claimed.id,
                    lease_owner="stale-worker",
                    attempt=claimed.attempts,
                )
                is None
            )
            assert (
                await fenced.complete_deletion_for_job(
                    workspace_id=WORKSPACE_ID,
                    document_id=DOCUMENT_ID,
                    revision=1,
                    job_id=claimed.id,
                    lease_owner="worker-1",
                    attempt=claimed.attempts + 1,
                )
                is None
            )
            await connection.rollback()

        async def connect(_settings: PostgreSQLSettings) -> psycopg.AsyncConnection:  # type: ignore[type-arg]
            return await _connect(document_postgres)

        monkeypatch.setattr(worker_service, "connect_postgres", connect)
        rag = SimpleNamespace(
            adelete_by_doc_id=AsyncMock(),
            ainsert=AsyncMock(),
        )
        processor = _processor(document_postgres, rag)
        await processor.process(claimed)

        rag.adelete_by_doc_id.assert_awaited_once()
        rag.ainsert.assert_not_awaited()
        async with await _connect(document_postgres) as connection:
            cursor = await connection.execute(
                "SELECT d.status, d.active_revision, j.status, "
                "(SELECT count(*) FROM graph_blizz.document_revisions r "
                "WHERE r.document_id = d.id) "
                "FROM graph_blizz.documents d JOIN graph_blizz.jobs j "
                "ON j.id = %s WHERE d.id = %s",
                (pending_id, DOCUMENT_ID),
            )
            assert await cursor.fetchone() == ("DELETED", 1, "SUCCEEDED", 1)

            revisions = await connection.execute(
                "SELECT object_uri FROM graph_blizz.document_revisions "
                "WHERE document_id = %s",
                (DOCUMENT_ID,),
            )
            assert await revisions.fetchone() == ("s3://bucket/revision/1",)

    asyncio.run(scenario())


def _processor(
    settings: PostgreSQLSettings, rag: SimpleNamespace
) -> IndexingJobProcessor:
    parser = MagicMock()
    parser.parse.return_value = ParsedDocument(content="parsed source", chunks=())
    processor = object.__new__(IndexingJobProcessor)
    processor._settings = SimpleNamespace(
        postgres=settings,
        auth=SimpleNamespace(demo_owner_id=WORKSPACE_ID),
    )
    processor._object_store = MagicMock()
    processor._object_store.get_uri.return_value = b"source"
    processor._parsers = MagicMock()
    processor._parsers.get_parser.return_value = parser
    processor._runtimes = MagicMock()
    processor._runtimes.get = AsyncMock(return_value=SimpleNamespace(rag=rag))
    return processor


async def _connect(settings: PostgreSQLSettings) -> psycopg.AsyncConnection:  # type: ignore[type-arg]
    return await psycopg.AsyncConnection.connect(
        host=settings.host,
        port=settings.port,
        dbname=settings.database,
        user=settings.username,
        password=settings.password.get_secret_value(),
        sslmode=settings.ssl_mode,
    )
