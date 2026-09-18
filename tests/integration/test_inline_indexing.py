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
    object_store.get.return_value = b"# Heading\n\nBody\n"
    source_service = DocumentSourceService(MagicMock(), object_store)
    statuses: list[DocumentStatus] = []
    repository = MagicMock()

    async def transition_status(**kwargs: object) -> Document:
        status = kwargs["to_status"]
        assert isinstance(status, DocumentStatus)
        statuses.append(status)
        return replace(document, status=status)

    repository.transition_status = AsyncMock(side_effect=transition_status)
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

    object_store.get.assert_called_once_with(
        f"workspace/{workspace_id}/document/{document_id}/source"
    )
    runtime_registry.get.assert_awaited_once_with(workspace)
    rag.ainsert.assert_awaited_once_with("# Heading\n\nBody\n")
    assert statuses == [DocumentStatus.INDEXING, DocumentStatus.READY]
    assert ready.status is DocumentStatus.READY


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
    )
