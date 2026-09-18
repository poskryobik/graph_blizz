"""Unit coverage for original document source persistence."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from backend.documents import Document, DocumentSourceService, DocumentStatus

pytestmark = pytest.mark.unit

WORKSPACE_ID = UUID("12345678-1234-5678-1234-567812345678")
DOCUMENT_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
OBJECT_KEY = f"workspace/{WORKSPACE_ID}/document/{DOCUMENT_ID}/revision/1/source"


def test_store_persists_exact_bytes_before_metadata() -> None:
    events: list[str] = []
    store = MagicMock()
    store.uri.return_value = f"s3://documents/{OBJECT_KEY}"
    store.put.side_effect = lambda key, content: events.append("source")
    repository = AsyncMock()
    repository.create.side_effect = lambda **kwargs: (
        events.append("metadata") or _document(**kwargs)
    )
    service = DocumentSourceService(repository, store, id_factory=lambda: DOCUMENT_ID)

    document = asyncio.run(
        service.store(
            workspace_id=WORKSPACE_ID,
            source_key="guide.pdf",
            filename="guide.pdf",
            source_type="application/pdf",
            content=b"\x00exact source\xff",
        )
    )

    assert events == ["source", "metadata"]
    repository.commit.assert_awaited_once_with()
    store.put.assert_called_once_with(OBJECT_KEY, b"\x00exact source\xff")
    assert repository.create.await_args.kwargs == {
        "document_id": DOCUMENT_ID,
        "workspace_id": WORKSPACE_ID,
        "source_key": "guide.pdf",
        "filename": "guide.pdf",
        "source_type": "application/pdf",
        "object_uri": f"s3://documents/{OBJECT_KEY}",
        "content_hash": "e93b782cffbdf7bb44c184d66794dadb7b8a25da6460d27cdbf37efb592132d2",
    }
    assert document.status is DocumentStatus.UPLOADED


def test_store_removes_source_when_metadata_commit_fails() -> None:
    store = MagicMock()
    store.uri.return_value = f"s3://documents/{OBJECT_KEY}"
    repository = AsyncMock()
    repository.create.return_value = _document(
        document_id=DOCUMENT_ID,
        workspace_id=WORKSPACE_ID,
        source_key="guide.pdf",
        filename="guide.pdf",
        source_type="application/pdf",
        object_uri=f"s3://documents/{OBJECT_KEY}",
        content_hash="hash",
    )
    repository.commit.side_effect = RuntimeError("commit failed")
    service = DocumentSourceService(repository, store, id_factory=lambda: DOCUMENT_ID)

    with pytest.raises(RuntimeError, match="commit failed"):
        asyncio.run(
            service.store(
                workspace_id=WORKSPACE_ID,
                source_key="guide.pdf",
                filename="guide.pdf",
                source_type="application/pdf",
                content=b"source",
            )
        )

    repository.rollback.assert_awaited_once_with()
    store.delete.assert_called_once_with(OBJECT_KEY)


def test_key_has_workspace_document_and_revision() -> None:
    key = DocumentSourceService.object_key(WORKSPACE_ID, DOCUMENT_ID)

    assert key == OBJECT_KEY
    assert "/revision/1/" in key


def _document(**values: object) -> Document:
    now = datetime(2026, 9, 17, tzinfo=UTC)
    return Document(
        id=values["document_id"],  # type: ignore[arg-type]
        workspace_id=values["workspace_id"],  # type: ignore[arg-type]
        source_key=values["source_key"],  # type: ignore[arg-type]
        filename=values["filename"],  # type: ignore[arg-type]
        source_type=values["source_type"],  # type: ignore[arg-type]
        object_uri=values["object_uri"],  # type: ignore[arg-type]
        content_hash=values["content_hash"],  # type: ignore[arg-type]
        status=DocumentStatus.UPLOADED,
        created_at=now,
        updated_at=now,
    )
