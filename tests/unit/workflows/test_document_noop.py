"""Unit coverage for idempotent document upsert decisions."""

import asyncio
from datetime import UTC, datetime
from hashlib import sha256
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from backend.documents import (
    Document,
    DocumentSourceService,
    DocumentStatus,
    DocumentUpsertAction,
)

pytestmark = pytest.mark.unit
WORKSPACE_ID = UUID("12345678-1234-5678-1234-567812345678")
DOCUMENT_ID = UUID("aaaaaaaa-1234-5678-1234-567812345678")
NOW = datetime(2026, 9, 18, tzinfo=UTC)
CONTENT = b"same source"


def test_identical_upsert_is_unchanged_without_revision_source_or_job() -> None:
    document = _document(content_hash=sha256(CONTENT).hexdigest())
    repository = AsyncMock()
    repository.lock_for_upsert.return_value = document
    object_store = MagicMock()
    jobs = AsyncMock()

    result = asyncio.run(
        DocumentSourceService(repository, object_store).upsert_for_indexing(
            jobs=jobs,
            workspace_id=WORKSPACE_ID,
            document_id=DOCUMENT_ID,
            source_key=f"upsert-{DOCUMENT_ID}",
            filename="notes.md",
            source_type="text/markdown",
            content=CONTENT,
        )
    )

    assert result.action is DocumentUpsertAction.UNCHANGED
    assert result.document is document
    assert result.job is None
    repository.lock_for_upsert.assert_awaited_once_with(
        workspace_id=WORKSPACE_ID, document_id=DOCUMENT_ID
    )
    repository.create.assert_not_awaited()
    repository.add_revision.assert_not_awaited()
    jobs.create_indexing.assert_not_awaited()
    object_store.put.assert_not_called()
    repository.commit.assert_awaited_once_with()


def _document(*, content_hash: str) -> Document:
    return Document(
        id=DOCUMENT_ID,
        workspace_id=WORKSPACE_ID,
        source_key=f"upsert-{DOCUMENT_ID}",
        filename="notes.md",
        source_type="text/markdown",
        object_uri="s3://private/source",
        content_hash=content_hash,
        status=DocumentStatus.READY,
        created_at=NOW,
        updated_at=NOW,
    )
