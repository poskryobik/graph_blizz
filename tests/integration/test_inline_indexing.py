"""Integration coverage across source, parser, and indexing boundaries."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from backend.documents import Document, DocumentSourceService, DocumentStatus
from backend.indexing import IndexingService
from backend.parsers import create_default_parser_registry
from backend.security import AuthorizedWorkspaceContext, PrincipalType

pytestmark = pytest.mark.integration


def test_inline_indexing_uses_stored_markdown_and_authorized_runtime() -> None:
    workspace_id = UUID("12345678-1234-5678-1234-567812345678")
    document_id = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    document = _document(workspace_id, document_id)
    object_store = MagicMock()
    object_store.get_uri.return_value = b"# Heading\n\nBody\n"
    source_service = DocumentSourceService(MagicMock(), object_store)
    statuses: list[DocumentStatus] = []
    repository = MagicMock()

    async def transition_status(**kwargs: object) -> Document:
        status = kwargs["to_status"]
        assert isinstance(status, DocumentStatus)
        statuses.append(status)
        return replace(document, status=status)

    repository.transition_status = AsyncMock(side_effect=transition_status)
    repository.commit = AsyncMock()
    rag = SimpleNamespace(ainsert=AsyncMock())
    runtime_registry = MagicMock()
    runtime_registry.get = AsyncMock(return_value=SimpleNamespace(rag=rag))
    workspace = AuthorizedWorkspaceContext(
        principal_id="demo-owner",
        principal_type=PrincipalType.USER,
        workspace_id=workspace_id,
        storage_key="ws_authorized_only",
        permissions=frozenset(),
    )
    service = IndexingService(
        repository,
        source_service,
        create_default_parser_registry(),
        runtime_registry,
    )

    ready = asyncio.run(service.index(workspace, document))

    object_store.get_uri.assert_called_once_with(document.object_uri)
    runtime_registry.get.assert_awaited_once_with(workspace)
    rag.ainsert.assert_awaited_once_with("# Heading\n\nBody\n")
    assert statuses == [DocumentStatus.INDEXING, DocumentStatus.READY]
    assert repository.commit.await_count == 2
    assert ready.status is DocumentStatus.READY


def test_failed_status_survives_request_transaction_rollback() -> None:
    """Committed lifecycle boundaries are not undone by the later HTTP error."""
    workspace_id = UUID("12345678-1234-5678-1234-567812345678")
    document_id = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    document = _document(workspace_id, document_id)
    committed = document.status
    staged = committed
    repository = MagicMock()

    async def transition_status(**kwargs: object) -> Document:
        nonlocal staged
        expected = kwargs["from_status"]
        target = kwargs["to_status"]
        assert staged is expected
        assert isinstance(target, DocumentStatus)
        staged = target
        return replace(document, status=target)

    async def commit() -> None:
        nonlocal committed
        committed = staged

    async def rollback() -> None:
        nonlocal staged
        staged = committed

    repository.transition_status = AsyncMock(side_effect=transition_status)
    repository.commit = AsyncMock(side_effect=commit)
    repository.rollback = AsyncMock(side_effect=rollback)
    object_store = MagicMock()
    object_store.get_uri.return_value = b"not utf-8: \xff"
    service = IndexingService(
        repository,
        DocumentSourceService(repository, object_store),
        create_default_parser_registry(),
        MagicMock(),
    )
    workspace = AuthorizedWorkspaceContext(
        principal_id="demo-owner",
        principal_type=PrincipalType.USER,
        workspace_id=workspace_id,
        storage_key="ws_authorized_only",
        permissions=frozenset(),
    )

    with pytest.raises(UnicodeDecodeError):
        asyncio.run(service.index(workspace, document))
    # FastAPI's connection dependency exits under HTTPException and rolls back
    # anything still pending; FAILED must already be its own committed boundary.
    asyncio.run(repository.rollback())

    assert committed is DocumentStatus.FAILED
    assert staged is DocumentStatus.FAILED
    assert repository.commit.await_count == 2


def _document(workspace_id: UUID, document_id: UUID) -> Document:
    now = datetime(2026, 9, 18, tzinfo=UTC)
    return Document(
        id=document_id,
        workspace_id=workspace_id,
        source_key="guide.md",
        filename="guide.md",
        source_type="text/markdown",
        object_uri="s3://documents/source",
        content_hash="a" * 64,
        status=DocumentStatus.UPLOADED,
        created_at=now,
        updated_at=now,
        active_revision=1,
    )
