"""Component coverage for indexing-job replay semantics."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call
from uuid import UUID

import pytest

from backend.documents import Document, DocumentStatus
from backend.indexing.provenance import decode_chunk_provenance
from backend.jobs import Job, JobStatus, JobType
from backend.parsers import TreeSitterParser
from backend.security import AuthorizedWorkspaceContext, PrincipalType
from backend.worker import service as worker_service
from backend.worker.runner import Worker
from backend.worker.service import IndexingJobProcessor

pytestmark = pytest.mark.unit
NOW = datetime(2026, 9, 18, 12, tzinfo=UTC)


class _ConnectionContext:
    def __init__(self, connection: AsyncMock) -> None:
        self._connection = connection

    async def __aenter__(self) -> AsyncMock:
        return self._connection

    async def __aexit__(self, *args: object) -> None:
        return None


def test_ready_revision_after_crash_is_replayed_as_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A recovered RUNNING job must complete without re-indexing READY data."""
    job = _running_job()
    recovered = replace(
        job,
        status=JobStatus.RETRY,
        lease_owner=None,
        lease_expires_at=None,
        heartbeat_at=None,
    )
    ready = Document(
        id=job.document_id,
        workspace_id=job.document_id,
        source_key="source",
        filename="document.txt",
        source_type="text/plain",
        object_uri="s3://bucket/document.txt",
        content_hash="hash",
        status=DocumentStatus.READY,
        created_at=NOW,
        updated_at=NOW,
        active_revision=job.document_revision,
    )
    connection = AsyncMock()
    connection.execute.return_value.fetchone.return_value = (ready.workspace_id,)
    documents = MagicMock()
    documents.get_revision = AsyncMock(return_value=ready)
    indexing_service = MagicMock()
    monkeypatch.setattr(
        worker_service,
        "connect_postgres",
        AsyncMock(return_value=_ConnectionContext(connection)),
    )
    monkeypatch.setattr(
        worker_service, "LeasedDocumentRepository", lambda _connection, _job: documents
    )
    monkeypatch.setattr(worker_service, "IndexingService", indexing_service)

    settings = MagicMock()
    processor = object.__new__(IndexingJobProcessor)
    processor._settings = settings
    processor._runtimes = MagicMock()
    processor._object_store = MagicMock()
    processor._parsers = MagicMock()
    store = AsyncMock()
    store.recover_expired.return_value = [recovered]
    store.claim_next.return_value = job

    worker = Worker(
        store,
        processor,
        owner="worker-1",
        lease_for=timedelta(minutes=1),
        heartbeat_every=10,
        retry_after=timedelta(seconds=5),
        poll_every=0.01,
    )
    worked = asyncio.run(worker.run_once())

    assert worked is True
    documents.get_revision.assert_awaited_once_with(
        ready.workspace_id, job.document_id, job.document_revision
    )
    connection.execute.assert_awaited()
    indexing_service.assert_not_called()
    store.succeed.assert_awaited_once_with(
        job_id=job.id, owner="worker-1", attempt=job.attempts
    )
    assert store.method_calls[:2] == [
        call.recover_expired(retry_after=timedelta(seconds=5)),
        call.claim_next(owner="worker-1", lease_for=timedelta(minutes=1)),
    ]


def test_replacement_code_chunks_use_job_target_revision() -> None:
    job = replace(_running_job(), document_revision=2)
    active = _document(active_revision=1, status=DocumentStatus.UPDATING)
    replacement = replace(
        active,
        object_uri="s3://bucket/revision/2",
        content_hash="b" * 64,
    )
    documents = MagicMock()
    documents.complete_replacement_for_job = AsyncMock(return_value=replacement)
    documents.commit = AsyncMock()
    sources = MagicMock()
    sources.read.side_effect = [
        b"def old():\n    return 1\n",
        b"def new():\n    return 2\n",
    ]
    rag = SimpleNamespace(adelete_by_doc_id=AsyncMock(), ainsert=AsyncMock())
    processor = object.__new__(IndexingJobProcessor)
    processor._parsers = MagicMock()
    processor._parsers.get_parser.return_value = TreeSitterParser(
        "python", "text/x-python"
    )
    processor._runtimes = MagicMock()
    processor._runtimes.get = AsyncMock(return_value=SimpleNamespace(rag=rag))

    asyncio.run(
        processor._replace(
            documents=documents,
            sources=sources,
            context=_workspace(),
            active=active,
            replacement=replacement,
            job=job,
        )
    )

    assert all(
        "-r2-" in document_id for document_id in rag.ainsert.await_args.kwargs["ids"]
    )
    citations = rag.ainsert.await_args.kwargs["file_paths"]
    decoded = [decode_chunk_provenance(value) for value in citations]
    assert all(value is not None and value["revision"] == 2 for value in decoded)


def _workspace() -> AuthorizedWorkspaceContext:
    return AuthorizedWorkspaceContext(
        principal_id="owner",
        principal_type=PrincipalType.USER,
        workspace_id=UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
        storage_key="ws_test",
        permissions=frozenset(),
    )


def _document(*, active_revision: int, status: DocumentStatus) -> Document:
    document_id = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
    return Document(
        id=document_id,
        workspace_id=document_id,
        source_key="source.py",
        filename="source.py",
        source_type="text/x-python",
        object_uri="s3://bucket/revision/1",
        content_hash="a" * 64,
        status=status,
        created_at=NOW,
        updated_at=NOW,
        active_revision=active_revision,
    )


def _running_job() -> Job:
    return Job(
        id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        document_id=UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
        document_revision=1,
        type=JobType.INDEX_DOCUMENT,
        status=JobStatus.RUNNING,
        attempts=2,
        max_attempts=3,
        available_at=NOW,
        created_at=NOW,
        updated_at=NOW,
        lease_owner="worker-1",
        lease_expires_at=NOW + timedelta(minutes=1),
        heartbeat_at=NOW,
        started_at=NOW,
    )
