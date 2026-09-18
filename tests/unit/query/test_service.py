"""Unit coverage for framework-independent query orchestration."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from lightrag import QueryParam

from backend.documents import Document, DocumentStatus
from backend.query import QueryService
from backend.security import AuthorizedWorkspaceContext, PrincipalType

pytestmark = pytest.mark.unit
WORKSPACE_ID = UUID("12345678-1234-5678-1234-567812345678")
OTHER_WORKSPACE_ID = UUID("99999999-9999-4999-8999-999999999999")
DOCUMENT_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
REQUEST_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")


def test_query_uses_authorized_runtime_and_returns_only_ready_workspace_sources() -> (
    None
):
    repository = MagicMock()
    repository.list = AsyncMock(
        return_value=[
            _document(),
            replace(_document(), id=UUID(int=2), status=DocumentStatus.FAILED),
            replace(_document(), id=UUID(int=3), workspace_id=OTHER_WORKSPACE_ID),
        ]
    )
    rag = SimpleNamespace(aquery=AsyncMock(return_value="Grounded answer"))
    runtimes = MagicMock()
    runtimes.get = AsyncMock(return_value=SimpleNamespace(rag=rag))
    service = QueryService(
        repository,
        runtimes,
        request_id_factory=lambda: REQUEST_ID,
    )

    result = asyncio.run(service.query(_workspace(), "  What is Graph RAG?  "))

    runtimes.get.assert_awaited_once_with(_workspace())
    rag.aquery.assert_awaited_once()
    assert rag.aquery.await_args.args == ("What is Graph RAG?",)
    assert rag.aquery.await_args.kwargs["param"] == QueryParam(stream=False)
    repository.list.assert_awaited_once_with(WORKSPACE_ID)
    assert result.answer == "Grounded answer"
    assert result.workspace_id == WORKSPACE_ID
    assert result.request_id == REQUEST_ID
    assert [(source.document_id, source.filename) for source in result.sources] == [
        (DOCUMENT_ID, "guide.md")
    ]


def test_empty_query_is_rejected_before_runtime_or_repository_access() -> None:
    repository = MagicMock()
    runtimes = MagicMock()
    service = QueryService(repository, runtimes)

    with pytest.raises(ValueError, match="must not be empty"):
        asyncio.run(service.query(_workspace(), " \n "))

    runtimes.get.assert_not_called()
    repository.list.assert_not_called()


def test_runtime_failure_is_preserved_without_source_lookup() -> None:
    error = RuntimeError("private namespace details")
    repository = MagicMock()
    runtimes = MagicMock()
    runtimes.get = AsyncMock(side_effect=error)

    with pytest.raises(RuntimeError) as caught:
        asyncio.run(QueryService(repository, runtimes).query(_workspace(), "question"))

    assert caught.value is error
    repository.list.assert_not_called()


def _workspace() -> AuthorizedWorkspaceContext:
    return AuthorizedWorkspaceContext(
        principal_id="demo-owner",
        principal_type=PrincipalType.USER,
        workspace_id=WORKSPACE_ID,
        storage_key="ws_server_generated",
        permissions=frozenset(),
    )


def _document() -> Document:
    now = datetime(2026, 9, 18, tzinfo=UTC)
    return Document(
        id=DOCUMENT_ID,
        workspace_id=WORKSPACE_ID,
        source_key="private-key",
        filename="guide.md",
        source_type="text/markdown",
        object_uri="s3://private/source",
        content_hash="a" * 64,
        status=DocumentStatus.READY,
        created_at=now,
        updated_at=now,
    )
