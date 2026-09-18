"""Unit coverage for immutable, monotonically numbered document revisions."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from backend.documents import (
    Document,
    DocumentRepository,
    DocumentRevision,
    DocumentSourceService,
    DocumentStatus,
)

pytestmark = pytest.mark.unit

DOCUMENT_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
WORKSPACE_ID = UUID("12345678-1234-5678-1234-567812345678")
CREATED_AT = datetime(2026, 9, 18, tzinfo=UTC)


def test_repository_locks_document_and_allocates_next_revision() -> None:
    lock_cursor = AsyncMock()
    lock_cursor.fetchone.return_value = (DOCUMENT_ID,)
    sequence_cursor = AsyncMock()
    sequence_cursor.fetchone.return_value = (3,)
    insert_cursor = AsyncMock()
    insert_cursor.fetchone.return_value = (
        DOCUMENT_ID,
        3,
        "s3://documents/revision/3/source",
        "c" * 64,
        CREATED_AT,
    )
    connection = AsyncMock()
    connection.execute.side_effect = [lock_cursor, sequence_cursor, insert_cursor]
    repository = DocumentRepository(connection)

    result = asyncio.run(
        repository.add_revision(
            workspace_id=WORKSPACE_ID,
            document_id=DOCUMENT_ID,
            content_hash="c" * 64,
            object_uri_factory=lambda revision: (
                f"s3://documents/revision/{revision}/source"
            ),
        )
    )

    assert result is not None
    assert result.revision == 3
    calls = connection.execute.await_args_list
    assert "FOR UPDATE" in calls[0].args[0]
    assert "MAX(revision)" in calls[1].args[0]
    assert calls[2].args[1] == (
        DOCUMENT_ID,
        3,
        "s3://documents/revision/3/source",
        "c" * 64,
    )


def test_source_service_stores_revision_under_immutable_numbered_key() -> None:
    document = _document()
    repository = AsyncMock()
    repository.add_revision.side_effect = _revision_from_factory
    store = MagicMock()
    store.uri.side_effect = lambda key: f"s3://documents/{key}"
    service = DocumentSourceService(repository, store)

    revision = asyncio.run(service.add_revision(document=document, content=b"changed"))

    expected_key = DocumentSourceService.object_key(WORKSPACE_ID, DOCUMENT_ID, 2)
    store.put.assert_called_once_with(expected_key, b"changed")
    assert revision.revision == 2
    assert revision.object_uri == f"s3://documents/{expected_key}"
    assert revision.content_hash == (
        "d67e2e944994496c8d8ec76eed0cf9f09679448d584b532bebf941852a37f5ed"
    )
    repository.commit.assert_awaited_once_with()


def test_numbered_object_keys_reject_non_positive_revisions() -> None:
    first = DocumentSourceService.object_key(WORKSPACE_ID, DOCUMENT_ID, 1)
    second = DocumentSourceService.object_key(WORKSPACE_ID, DOCUMENT_ID, 2)

    assert first != second
    assert first.endswith("/revision/1/source")
    assert second.endswith("/revision/2/source")
    with pytest.raises(ValueError, match="positive"):
        DocumentSourceService.object_key(WORKSPACE_ID, DOCUMENT_ID, 0)


async def _revision_from_factory(**kwargs: object) -> DocumentRevision:
    factory = kwargs["object_uri_factory"]
    assert callable(factory)
    uri = factory(2)
    return DocumentRevision(
        document_id=DOCUMENT_ID,
        revision=2,
        object_uri=uri,
        content_hash=kwargs["content_hash"],  # type: ignore[arg-type]
        created_at=CREATED_AT,
    )


def _document() -> Document:
    return Document(
        id=DOCUMENT_ID,
        workspace_id=WORKSPACE_ID,
        source_key="guide.md",
        filename="guide.md",
        source_type="text/markdown",
        object_uri="s3://documents/revision/1/source",
        content_hash="a" * 64,
        status=DocumentStatus.READY,
        created_at=CREATED_AT,
        updated_at=CREATED_AT,
        active_revision=1,
    )
