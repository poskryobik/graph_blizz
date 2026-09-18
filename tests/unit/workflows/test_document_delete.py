"""Focused durable document deletion workflow coverage."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from lightrag.utils import compute_mdhash_id

from backend.documents import Document, DocumentSourceService, DocumentStatus
from backend.jobs import Job, JobStatus, JobType
from backend.parsers import ParsedDocument
from backend.security import AuthorizedWorkspaceContext, PrincipalType
from backend.worker.service import IndexingJobProcessor

pytestmark = pytest.mark.unit
NOW = datetime(2026, 9, 18, tzinfo=UTC)
WORKSPACE_ID = UUID("12345678-1234-5678-1234-567812345678")
DOCUMENT_ID = UUID("aaaaaaaa-1234-5678-1234-567812345678")
JOB_ID = UUID("bbbbbbbb-1234-5678-1234-567812345678")


def test_delete_transitions_and_creates_job_before_commit() -> None:
    events: list[str] = []
    ready = _document(DocumentStatus.READY)
    deleting = replace(ready, status=DocumentStatus.DELETING)
    job = _job(JobStatus.PENDING, attempts=0)
    repository = MagicMock()
    repository.lock_for_upsert = AsyncMock(return_value=ready)
    repository.transition_status = AsyncMock(
        side_effect=lambda **_kwargs: events.append("deleting") or deleting
    )
    repository.commit = AsyncMock(side_effect=lambda: events.append("commit"))
    repository.rollback = AsyncMock()
    jobs = MagicMock()
    jobs.create_deletion = AsyncMock(
        side_effect=lambda **_kwargs: events.append("job") or job
    )

    result = asyncio.run(
        DocumentSourceService(repository, MagicMock()).delete_for_indexing(
            jobs=jobs, workspace_id=WORKSPACE_ID, document_id=DOCUMENT_ID
        )
    )

    assert result.document is deleting
    assert result.job is job
    assert events == ["deleting", "job", "commit"]
    jobs.create_deletion.assert_awaited_once_with(
        document_id=DOCUMENT_ID, document_revision=1
    )


def test_delete_is_idempotent_while_deleting_and_after_deleted() -> None:
    deletion_job = _job(JobStatus.PENDING, attempts=0)
    for status, active_job, expected in (
        (DocumentStatus.DELETING, deletion_job, deletion_job),
        (DocumentStatus.DELETED, None, None),
    ):
        repository = MagicMock()
        repository.lock_for_upsert = AsyncMock(return_value=_document(status))
        repository.commit = AsyncMock()
        repository.rollback = AsyncMock()
        jobs = MagicMock()
        jobs.get_active_for_document = AsyncMock(return_value=active_job)
        jobs.create_deletion = AsyncMock()

        result = asyncio.run(
            DocumentSourceService(repository, MagicMock()).delete_for_indexing(
                jobs=jobs, workspace_id=WORKSPACE_ID, document_id=DOCUMENT_ID
            )
        )

        assert result.job is expected
        jobs.create_deletion.assert_not_awaited()


def test_delete_rolls_back_if_job_creation_fails() -> None:
    repository = MagicMock()
    repository.lock_for_upsert = AsyncMock(return_value=_document(DocumentStatus.READY))
    repository.transition_status = AsyncMock(
        return_value=_document(DocumentStatus.DELETING)
    )
    repository.rollback = AsyncMock()
    repository.commit = AsyncMock()
    jobs = MagicMock()
    jobs.create_deletion = AsyncMock(side_effect=RuntimeError("job failed"))

    with pytest.raises(RuntimeError, match="job failed"):
        asyncio.run(
            DocumentSourceService(repository, MagicMock()).delete_for_indexing(
                jobs=jobs, workspace_id=WORKSPACE_ID, document_id=DOCUMENT_ID
            )
        )

    repository.rollback.assert_awaited_once()
    repository.commit.assert_not_awaited()


def test_worker_deletes_without_reindex_before_fenced_completion() -> None:
    events: list[str] = []
    documents = MagicMock()
    documents.complete_deletion_for_job = AsyncMock(
        side_effect=lambda **_kwargs: (
            events.append("complete") or _document(DocumentStatus.DELETED)
        )
    )
    documents.commit = AsyncMock(side_effect=lambda: events.append("commit"))
    sources = MagicMock()
    sources.read.return_value = b"source"
    parser = MagicMock()
    parser.parse.return_value = ParsedDocument(content="parsed source", chunks=())
    rag = SimpleNamespace(
        adelete_by_doc_id=AsyncMock(side_effect=lambda _id: events.append("delete")),
        ainsert=AsyncMock(),
    )
    processor = object.__new__(IndexingJobProcessor)
    processor._parsers = MagicMock()
    processor._parsers.get_parser.return_value = parser
    processor._runtimes = MagicMock()
    processor._runtimes.get = AsyncMock(return_value=SimpleNamespace(rag=rag))

    asyncio.run(
        processor._delete(
            documents=documents,
            sources=sources,
            context=_context(),
            document=_document(DocumentStatus.DELETING),
            job=_job(JobStatus.RUNNING, attempts=1),
        )
    )

    assert events == ["delete", "complete", "commit"]
    rag.adelete_by_doc_id.assert_awaited_once_with(
        compute_mdhash_id("parsed source", prefix="doc-")
    )
    rag.ainsert.assert_not_awaited()


def test_worker_failure_does_not_complete_deletion() -> None:
    documents = MagicMock()
    documents.complete_deletion_for_job = AsyncMock()
    documents.commit = AsyncMock()
    sources = MagicMock()
    sources.read.return_value = b"source"
    parser = MagicMock()
    parser.parse.return_value = ParsedDocument(content="parsed source", chunks=())
    rag = SimpleNamespace(
        adelete_by_doc_id=AsyncMock(side_effect=RuntimeError("delete failed"))
    )
    processor = object.__new__(IndexingJobProcessor)
    processor._parsers = MagicMock()
    processor._parsers.get_parser.return_value = parser
    processor._runtimes = MagicMock()
    processor._runtimes.get = AsyncMock(return_value=SimpleNamespace(rag=rag))

    with pytest.raises(RuntimeError, match="delete failed"):
        asyncio.run(
            processor._delete(
                documents=documents,
                sources=sources,
                context=_context(),
                document=_document(DocumentStatus.DELETING),
                job=_job(JobStatus.RUNNING, attempts=1),
            )
        )

    documents.complete_deletion_for_job.assert_not_awaited()
    documents.commit.assert_not_awaited()


def _document(status: DocumentStatus) -> Document:
    return Document(
        id=DOCUMENT_ID,
        workspace_id=WORKSPACE_ID,
        source_key="stable",
        filename="notes.md",
        source_type="text/markdown",
        object_uri="s3://revision/1",
        content_hash="hash",
        status=status,
        created_at=NOW,
        updated_at=NOW,
        active_revision=1,
    )


def _job(status: JobStatus, *, attempts: int) -> Job:
    running = status is JobStatus.RUNNING
    return Job(
        id=JOB_ID,
        document_id=DOCUMENT_ID,
        document_revision=1,
        type=JobType.DELETE_DOCUMENT,
        status=status,
        attempts=attempts,
        max_attempts=3,
        available_at=NOW,
        created_at=NOW,
        updated_at=NOW,
        lease_owner="worker-1" if running else None,
        lease_expires_at=NOW + timedelta(minutes=1) if running else None,
        heartbeat_at=NOW if running else None,
        started_at=NOW if attempts else None,
    )


def _context() -> AuthorizedWorkspaceContext:
    return AuthorizedWorkspaceContext(
        principal_id=WORKSPACE_ID,
        principal_type=PrincipalType.USER,
        workspace_id=WORKSPACE_ID,
        storage_key="workspace",
        permissions=frozenset(),
    )
