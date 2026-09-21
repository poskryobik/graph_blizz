"""Cross-component persistence gate for durable workspace reindex."""

import asyncio
from collections.abc import Iterator
from datetime import timedelta
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.config import EmbeddingSettings, PostgreSQLSettings
from backend.index_versions import active_index_contract
from backend.jobs import JobErrorCode, JobRepository, JobStatus, JobType
from backend.maintenance import WorkspaceReindexService
from backend.parsers import ParsedDocument
from backend.storage import ObjectStorageError
from backend.worker.service import (
    IndexingJobProcessor,
    PostgreSQLJobStore,
    connect_postgres,
)
from backend.workspaces import WorkspaceRepository
from tests.integration.worker_db import live_worker_postgres

pytestmark = pytest.mark.e2e
ORIGINAL_URI = "s3://graph-blizz/workspaces/reindex/original.md"
ORIGINAL = b"# Stored original\n\nRecovered from MinIO.\n"


@pytest.fixture(scope="module")
def postgres() -> Iterator[PostgreSQLSettings]:
    """Provide migrated PostgreSQL or skip when Docker infrastructure is absent."""
    yield from live_worker_postgres()


def test_maintenance_reconciles_contract_and_worker_recovers_from_failure(
    postgres: PostgreSQLSettings,
) -> None:
    """Reconcile stale metadata, retry a failed read, and finish after restart."""

    async def scenario() -> None:
        embedding = EmbeddingSettings(model="replacement-embedding")
        contract = active_index_contract(embedding)
        async with await connect_postgres(postgres) as setup:
            seeded = await setup.execute(
                "WITH w AS (INSERT INTO graph_blizz.workspaces "
                "(name, slug, index_schema_version, embedding_profile) "
                "VALUES ('Reindex', 'e2e-reindex', 0, '{\"model\":\"legacy\"}') "
                "RETURNING id), d AS (INSERT INTO graph_blizz.documents "
                "(workspace_id, source_key, filename, source_type, status, "
                "active_revision) SELECT id, 'source', 'guide.md', "
                "'text/markdown', 'READY', 1 FROM w RETURNING id, workspace_id), "
                "r AS (INSERT INTO graph_blizz.document_revisions "
                "(document_id, revision, object_uri, content_hash) "
                "SELECT id, 1, %s, 'hash' FROM d) "
                "SELECT id, workspace_id FROM d",
                (ORIGINAL_URI,),
            )
            row = await seeded.fetchone()
            assert row is not None
            document_id, workspace_id = row
            await setup.commit()

        async with await connect_postgres(postgres) as maintenance_connection:
            service = WorkspaceReindexService(
                WorkspaceRepository(maintenance_connection),
                JobRepository(maintenance_connection),
                contract,
            )
            created = await service.enqueue(workspace_id)
            assert len(created) == 1
            assert created[0].type is JobType.REINDEX_DOCUMENT

        store = PostgreSQLJobStore(postgres)
        first = await store.claim_next(
            owner="failed-worker", lease_for=timedelta(minutes=1)
        )
        assert first is not None
        failed_store = MagicMock()
        failed_store.get_uri.side_effect = ObjectStorageError("MinIO unavailable")
        failed_processor, _ = _processor(postgres, embedding, failed_store)
        with pytest.raises(ObjectStorageError, match="MinIO unavailable"):
            await failed_processor.process(first)
        failed_store.get_uri.assert_called_once_with(ORIGINAL_URI)
        retried = await store.fail(
            job_id=first.id,
            owner="failed-worker",
            attempt=first.attempts,
            error_code=JobErrorCode.DEPENDENCY_UNAVAILABLE,
            retry_after=timedelta(0),
        )
        assert retried is not None
        assert retried.status is JobStatus.RETRY

        replacement = await store.claim_next(
            owner="replacement-worker", lease_for=timedelta(minutes=1)
        )
        assert replacement is not None
        assert replacement.id == created[0].id
        object_store = MagicMock()
        object_store.get_uri.return_value = ORIGINAL
        processor, rag = _processor(postgres, embedding, object_store)
        await processor.process(replacement)
        object_store.get_uri.assert_called_once_with(ORIGINAL_URI)
        rag.ainsert.assert_awaited_once_with(ORIGINAL.decode())

        async with await connect_postgres(postgres) as observed:
            result = await observed.execute(
                "SELECT w.index_schema_version, w.embedding_profile, "
                "r.index_schema_version, r.requires_reindex, j.status, j.attempts "
                "FROM graph_blizz.workspaces AS w "
                "JOIN graph_blizz.documents AS d ON d.workspace_id = w.id "
                "JOIN graph_blizz.document_revisions AS r "
                "ON r.document_id = d.id AND r.revision = d.active_revision "
                "JOIN graph_blizz.jobs AS j ON j.document_id = d.id "
                "WHERE d.id = %s",
                (document_id,),
            )
            assert await result.fetchone() == (
                contract.index_schema_version,
                contract.embedding_profile,
                contract.index_schema_version,
                False,
                JobStatus.SUCCEEDED.value,
                2,
            )

    asyncio.run(scenario())


def _processor(
    postgres: PostgreSQLSettings,
    embedding: EmbeddingSettings,
    object_store: MagicMock,
) -> tuple[IndexingJobProcessor, Any]:
    """Build an isolated worker process boundary with observable adapters."""
    parser = MagicMock()
    parser.parse.return_value = ParsedDocument(content=ORIGINAL.decode(), chunks=())
    rag = SimpleNamespace(ainsert=AsyncMock())
    runtimes = MagicMock()
    runtimes.get = AsyncMock(return_value=SimpleNamespace(rag=rag))
    processor = object.__new__(IndexingJobProcessor)
    processor._settings = cast(
        Any,
        SimpleNamespace(
            postgres=postgres,
            embedding=embedding,
            auth=SimpleNamespace(demo_owner_id="e2e-owner"),
        ),
    )
    processor._object_store = object_store
    processor._parsers = MagicMock()
    processor._parsers.get_parser.return_value = parser
    processor._runtimes = runtimes
    return processor, rag
