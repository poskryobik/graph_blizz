"""Focused replacement workflow coverage."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from lightrag.utils import compute_mdhash_id

from backend.documents import (
    Document,
    DocumentRevision,
    DocumentSourceService,
    DocumentStatus,
    DocumentUpsertAction,
)
from backend.jobs import Job, JobStatus, JobType
from backend.parsers import ParsedDocument
from backend.security import AuthorizedWorkspaceContext, PrincipalType
from backend.worker.service import IndexingJobProcessor

pytestmark = pytest.mark.unit
NOW = datetime(2026, 9, 18, tzinfo=UTC)
WORKSPACE_ID = UUID("12345678-1234-5678-1234-567812345678")
DOCUMENT_ID = UUID("aaaaaaaa-1234-5678-1234-567812345678")
JOB_ID = UUID("bbbbbbbb-1234-5678-1234-567812345678")


def test_changed_upsert_creates_revision_and_job_before_commit() -> None:
    events: list[str] = []
    active = _document(revision=1, status=DocumentStatus.READY)
    revision = DocumentRevision(DOCUMENT_ID, 2, "s3://revision/2", "new", NOW)
    job = _job()
    repository = MagicMock()
    repository.lock_for_upsert = AsyncMock(return_value=active)
    repository.add_revision = AsyncMock(
        side_effect=lambda **kwargs: (
            events.append("revision"),
            kwargs["object_uri_factory"](2),
            revision,
        )[-1]
    )
    repository.transition_status = AsyncMock(
        side_effect=lambda **kwargs: (
            events.append("updating") or replace(active, status=kwargs["to_status"])
        )
    )
    repository.commit = AsyncMock(side_effect=lambda: events.append("commit"))
    jobs = MagicMock()
    jobs.create_indexing = AsyncMock(
        side_effect=lambda **kwargs: events.append("job") or job
    )
    objects = MagicMock()
    objects.uri.side_effect = lambda key: f"s3://bucket/{key}"

    result = asyncio.run(
        DocumentSourceService(repository, objects).upsert_for_indexing(
            jobs=jobs,
            workspace_id=WORKSPACE_ID,
            document_id=DOCUMENT_ID,
            source_key="stable",
            filename="notes.md",
            source_type="text/markdown",
            content=b"new content",
        )
    )

    assert result.action is DocumentUpsertAction.UPDATED
    assert result.document.status is DocumentStatus.UPDATING
    assert result.document.active_revision == 1
    assert result.job is job
    assert events == ["revision", "updating", "job", "commit"]
    objects.put.assert_called_once()
    jobs.create_indexing.assert_awaited_once_with(
        document_id=DOCUMENT_ID, document_revision=2
    )


def test_worker_deletes_old_then_indexes_new_before_atomic_activation() -> None:
    events: list[str] = []
    documents = MagicMock()
    documents.complete_replacement_for_job = AsyncMock(
        side_effect=lambda **kwargs: (
            events.append("activate")
            or _document(revision=2, status=DocumentStatus.READY)
        )
    )
    documents.commit = AsyncMock(side_effect=lambda: events.append("commit"))
    sources = MagicMock()
    sources.read.side_effect = [b"old", b"new"]
    parser = MagicMock()
    parser.parse.side_effect = [
        ParsedDocument(content="parsed old", chunks=()),
        ParsedDocument(content="parsed new", chunks=()),
    ]
    rag = SimpleNamespace(
        adelete_by_doc_id=AsyncMock(side_effect=lambda _id: events.append("delete")),
        ainsert=AsyncMock(side_effect=lambda _content: events.append("insert")),
    )
    processor = object.__new__(IndexingJobProcessor)
    processor._parsers = MagicMock()
    processor._parsers.get_parser.return_value = parser
    processor._runtimes = MagicMock()
    processor._runtimes.get = AsyncMock(return_value=SimpleNamespace(rag=rag))

    asyncio.run(
        processor._replace(
            documents=documents,
            sources=sources,
            context=_context(),
            active=_document(revision=1, status=DocumentStatus.UPDATING),
            replacement=_document(revision=2, status=DocumentStatus.UPDATING),
            job=_job(),
        )
    )

    assert events == ["delete", "insert", "activate", "commit"]
    rag.adelete_by_doc_id.assert_awaited_once_with(
        compute_mdhash_id("parsed old", prefix="doc-")
    )
    rag.ainsert.assert_awaited_once_with("parsed new")


def _document(*, revision: int, status: DocumentStatus) -> Document:
    return Document(
        id=DOCUMENT_ID,
        workspace_id=WORKSPACE_ID,
        source_key="stable",
        filename="notes.md",
        source_type="text/markdown",
        object_uri=f"s3://revision/{revision}",
        content_hash=sha256(f"revision {revision}".encode()).hexdigest(),
        status=status,
        created_at=NOW,
        updated_at=NOW,
        active_revision=revision,
    )


def _job() -> Job:
    return Job(
        id=JOB_ID,
        document_id=DOCUMENT_ID,
        document_revision=2,
        type=JobType.INDEX_DOCUMENT,
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


def _context() -> AuthorizedWorkspaceContext:
    return AuthorizedWorkspaceContext(
        principal_id=WORKSPACE_ID,
        principal_type=PrincipalType.USER,
        workspace_id=WORKSPACE_ID,
        storage_key="workspace",
        permissions=frozenset(),
    )
