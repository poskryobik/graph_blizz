"""Unit coverage for durable workspace reindex."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from backend.documents import Document, DocumentStatus
from backend.index_versions import IndexContract
from backend.jobs import Job, JobStatus, JobType
from backend.jobs.repository import JobRepository
from backend.maintenance import WorkspaceReindexService
from backend.security import AuthorizedWorkspaceContext, PrincipalType
from backend.worker.service import IndexingJobProcessor

pytestmark = pytest.mark.unit

WORKSPACE_ID = UUID("12345678-1234-5678-1234-567812345678")
DOCUMENT_ID = UUID("22345678-1234-5678-1234-567812345678")
JOB_ID = UUID("32345678-1234-5678-1234-567812345678")
NOW = datetime(2026, 9, 21, tzinfo=UTC)


def test_maintenance_operation_commits_durable_jobs() -> None:
    workspaces = AsyncMock()
    jobs = AsyncMock()
    expected = [_job()]
    jobs.create_workspace_reindex.return_value = expected
    contract = IndexContract(2, '{"model":"v2"}')
    service = WorkspaceReindexService(workspaces, jobs, contract)

    assert asyncio.run(service.enqueue(WORKSPACE_ID)) == expected
    workspaces.ensure_index_contract.assert_awaited_once_with(
        WORKSPACE_ID,
        index_schema_version=2,
        embedding_profile='{"model":"v2"}',
    )
    jobs.create_workspace_reindex.assert_awaited_once_with(WORKSPACE_ID)
    jobs.commit.assert_awaited_once()
    jobs.rollback.assert_not_awaited()


def test_maintenance_operation_rolls_back_creation_failure() -> None:
    workspaces = AsyncMock()
    jobs = AsyncMock()
    jobs.create_workspace_reindex.side_effect = RuntimeError("database unavailable")
    service = WorkspaceReindexService(
        workspaces, jobs, IndexContract(2, '{"model":"v2"}')
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        asyncio.run(service.enqueue(WORKSPACE_ID))
    jobs.rollback.assert_awaited_once()


def test_maintenance_operation_rolls_back_contract_failure() -> None:
    workspaces = AsyncMock()
    workspaces.ensure_index_contract.side_effect = RuntimeError("contract unavailable")
    jobs = AsyncMock()
    service = WorkspaceReindexService(
        workspaces, jobs, IndexContract(2, '{"model":"v2"}')
    )

    with pytest.raises(RuntimeError, match="contract unavailable"):
        asyncio.run(service.enqueue(WORKSPACE_ID))
    jobs.create_workspace_reindex.assert_not_awaited()
    jobs.rollback.assert_awaited_once()


def test_repository_serializes_and_selects_only_stored_active_revisions() -> None:
    locked = AsyncMock()
    inserted = AsyncMock()
    inserted.fetchall.return_value = []
    connection = AsyncMock()
    connection.execute.side_effect = [locked, inserted]

    assert (
        asyncio.run(JobRepository(connection).create_workspace_reindex(WORKSPACE_ID))
        == []
    )

    lock_statement, lock_parameters = connection.execute.await_args_list[0].args
    assert "pg_advisory_xact_lock" in lock_statement
    assert lock_parameters == (f"workspace-reindex:{WORKSPACE_ID}",)
    statement, parameters = connection.execute.await_args_list[1].args
    assert "r.revision = d.active_revision" in statement
    assert "r.requires_reindex" in statement
    assert "active.status IN ('PENDING', 'RUNNING', 'RETRY')" in statement
    assert parameters == (JobType.REINDEX_DOCUMENT.value, WORKSPACE_ID)


def test_worker_reindexes_stored_revision_before_atomic_completion() -> None:
    job = _job()
    document = _document()
    documents = AsyncMock()
    documents.complete_reindex_for_job.return_value = replace(
        document, requires_reindex=False
    )
    sources = MagicMock()
    parsed = MagicMock()
    runtime = MagicMock()
    runtime.rag = MagicMock()
    runtimes = AsyncMock()
    runtimes.get.return_value = runtime
    processor = object.__new__(IndexingJobProcessor)
    processor._runtimes = runtimes
    processor._parse = MagicMock(return_value=parsed)
    context = AuthorizedWorkspaceContext(
        principal_id="owner",
        principal_type=PrincipalType.USER,
        workspace_id=WORKSPACE_ID,
        storage_key="workspace",
        permissions=frozenset(),
        index_schema_version=2,
        embedding_profile='{"model":"v2"}',
    )

    async def scenario() -> None:
        from backend.worker import service as worker_service

        original = worker_service.insert_parsed_document
        inserted = AsyncMock()
        worker_service.insert_parsed_document = inserted
        try:
            await processor._reindex(
                documents=documents,
                sources=sources,
                context=context,
                document=document,
                job=job,
            )
        finally:
            worker_service.insert_parsed_document = original
        inserted.assert_awaited_once_with(
            runtime.rag, document, parsed, revision=job.document_revision
        )

    asyncio.run(scenario())
    processor._parse.assert_called_once_with(sources, document)
    documents.complete_reindex_for_job.assert_awaited_once()
    assert (
        documents.complete_reindex_for_job.await_args.kwargs["index_schema_version"]
        == 2
    )
    documents.commit.assert_awaited_once()


def _job() -> Job:
    return Job(
        id=JOB_ID,
        document_id=DOCUMENT_ID,
        document_revision=1,
        type=JobType.REINDEX_DOCUMENT,
        status=JobStatus.RUNNING,
        attempts=1,
        max_attempts=3,
        available_at=NOW,
        created_at=NOW,
        updated_at=NOW,
        lease_owner="worker-1",
        lease_expires_at=NOW + timedelta(minutes=1),
        heartbeat_at=NOW,
        started_at=NOW,
    )


def _document() -> Document:
    return Document(
        id=DOCUMENT_ID,
        workspace_id=WORKSPACE_ID,
        source_key="source",
        filename="guide.md",
        source_type="text/markdown",
        object_uri="s3://documents/workspaces/document/revisions/1/source",
        content_hash="hash",
        status=DocumentStatus.READY,
        created_at=NOW,
        updated_at=NOW,
        active_revision=1,
        requires_reindex=True,
    )
