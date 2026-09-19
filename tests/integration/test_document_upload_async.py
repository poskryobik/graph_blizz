"""Integration coverage for atomic source, revision, and job persistence."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from backend.documents import Document, DocumentSourceService, DocumentStatus

pytestmark = pytest.mark.integration
WORKSPACE_ID = UUID("12345678-1234-5678-1234-567812345678")
DOCUMENT_ID = UUID("aaaaaaaa-1234-5678-1234-567812345678")
NOW = datetime(2026, 9, 18, 10, tzinfo=UTC)


def test_job_insert_failure_rolls_back_metadata_and_removes_source() -> None:
    repository = AsyncMock()
    repository.create.return_value = Document(
        id=DOCUMENT_ID,
        workspace_id=WORKSPACE_ID,
        source_key="upload-1",
        filename="notes.txt",
        source_type="text/plain",
        object_uri="s3://documents/source",
        content_hash="hash",
        status=DocumentStatus.UPLOADED,
        created_at=NOW,
        updated_at=NOW,
        active_revision=None,
    )
    jobs = AsyncMock()
    jobs.create_indexing.side_effect = RuntimeError("job insert failed")
    object_store = MagicMock()
    object_store.uri.return_value = "s3://documents/source"
    service = DocumentSourceService(
        repository, object_store, id_factory=lambda: DOCUMENT_ID
    )

    with pytest.raises(RuntimeError, match="job insert failed"):
        asyncio.run(
            service.store_for_indexing(
                jobs=jobs,
                workspace_id=WORKSPACE_ID,
                source_key="upload-1",
                filename="notes.txt",
                source_type="text/plain",
                content=b"notes",
            )
        )

    repository.rollback.assert_awaited_once()
    repository.commit.assert_not_awaited()
    object_store.delete.assert_called_once_with(
        f"workspace/{WORKSPACE_ID}/document/{DOCUMENT_ID}/revision/1/source"
    )


def test_document_revision_and_pending_job_commit_together() -> None:
    repository = AsyncMock()
    document = MagicMock(
        id=DOCUMENT_ID,
        active_revision=1,
    )
    repository.create.return_value = document
    job = MagicMock(document_id=DOCUMENT_ID, document_revision=1)
    jobs = AsyncMock()
    jobs.create_indexing.return_value = job
    object_store = MagicMock()
    object_store.uri.return_value = "s3://documents/source"
    service = DocumentSourceService(
        repository, object_store, id_factory=lambda: DOCUMENT_ID
    )

    result = asyncio.run(
        service.store_for_indexing(
            jobs=jobs,
            workspace_id=WORKSPACE_ID,
            source_key="upload-1",
            filename="notes.txt",
            source_type="text/plain",
            content=b"notes",
        )
    )

    assert result == (document, job)
    jobs.create_indexing.assert_awaited_once_with(
        document_id=DOCUMENT_ID, document_revision=1
    )
    repository.commit.assert_awaited_once()
    repository.rollback.assert_not_awaited()
