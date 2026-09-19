"""Application integration coverage for the Demo Graph RAG query API."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient

from backend import create_app
from backend.api.documents import get_document_repository
from backend.api.workspaces import get_workspace_repository
from backend.documents import Document, DocumentStatus
from backend.workspaces import Workspace, WorkspaceStatus

pytestmark = pytest.mark.integration


def test_demo_owner_queries_workspace_without_credentials_and_gets_sources() -> None:
    workspace_id = UUID("12345678-1234-5678-1234-567812345678")
    document_id = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    now = datetime(2026, 9, 18, tzinfo=UTC)
    workspace = Workspace(
        id=workspace_id,
        name="Knowledge",
        slug="knowledge",
        storage_key="ws_server_generated",
        status=WorkspaceStatus.ACTIVE,
        created_at=now,
        updated_at=now,
    )
    document = Document(
        id=document_id,
        workspace_id=workspace_id,
        source_key="private-key",
        filename="guide.md",
        source_type="text/markdown",
        object_uri="s3://private/source",
        content_hash="private-hash",
        status=DocumentStatus.READY,
        created_at=now,
        updated_at=now,
        active_revision=1,
    )
    workspaces = AsyncMock()
    workspaces.get.return_value = workspace
    documents = AsyncMock()
    documents.list.return_value = [document]
    rag = SimpleNamespace(aquery=AsyncMock(return_value="Graph RAG answer"))
    runtimes = MagicMock()
    runtimes.get = AsyncMock(return_value=SimpleNamespace(rag=rag))
    application = create_app()
    application.state.runtime_registry = runtimes

    async def workspace_repository():  # type: ignore[no-untyped-def]
        yield workspaces

    async def document_repository():  # type: ignore[no-untyped-def]
        yield documents

    application.dependency_overrides[get_workspace_repository] = workspace_repository
    application.dependency_overrides[get_document_repository] = document_repository

    async def exercise():  # type: ignore[no-untyped-def]
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            success = await client.post(
                f"/v1/workspaces/{workspace_id}/query",
                json={"query": "What does the guide say?"},
            )
            repeated = await client.post(
                f"/v1/workspaces/{workspace_id}/query",
                json={"query": "What does the guide say?"},
            )
            invalid = await client.post(
                f"/v1/workspaces/{workspace_id}/query",
                json={"query": "   ", "storage_key": "ws_attacker"},
            )
        return success, repeated, invalid

    success, repeated, invalid = asyncio.run(exercise())

    assert success.status_code == 200
    payload = success.json()
    assert payload["answer"] == "Graph RAG answer"
    assert payload["workspace_id"] == str(workspace_id)
    assert UUID(payload["request_id"])
    assert repeated.status_code == 200
    assert repeated.json()["request_id"] != payload["request_id"]
    assert payload["sources"] == [
        {"document_id": str(document_id), "filename": "guide.md"}
    ]
    assert {"storage_key", "source_key", "object_uri", "content_hash"}.isdisjoint(
        payload
    )
    assert invalid.status_code == 422
    context = runtimes.get.await_args.args[0]
    assert context.workspace_id == workspace_id
    assert context.storage_key == workspace.storage_key


def test_query_missing_workspace_and_runtime_error_are_safe() -> None:
    workspace_id = UUID("12345678-1234-5678-1234-567812345678")
    workspaces = AsyncMock()
    workspaces.get.return_value = None
    documents = AsyncMock()
    application = create_app()

    async def workspace_repository():  # type: ignore[no-untyped-def]
        yield workspaces

    async def document_repository():  # type: ignore[no-untyped-def]
        yield documents

    application.dependency_overrides[get_workspace_repository] = workspace_repository
    application.dependency_overrides[get_document_repository] = document_repository

    async def exercise():  # type: ignore[no-untyped-def]
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            return await client.post(
                f"/v1/workspaces/{workspace_id}/query", json={"query": "question"}
            )

    missing = asyncio.run(exercise())

    assert missing.status_code == 404
    assert "ws_" not in missing.text


def test_query_runtime_error_does_not_expose_internal_details() -> None:
    workspace_id = UUID("12345678-1234-5678-1234-567812345678")
    now = datetime(2026, 9, 18, tzinfo=UTC)
    workspace = Workspace(
        id=workspace_id,
        name="Knowledge",
        slug="knowledge",
        storage_key="ws_private_namespace",
        status=WorkspaceStatus.ACTIVE,
        created_at=now,
        updated_at=now,
    )
    workspaces = AsyncMock()
    workspaces.get.return_value = workspace
    documents = AsyncMock()
    runtimes = MagicMock()
    runtimes.get = AsyncMock(
        side_effect=RuntimeError("ws_private_namespace secret-token")
    )
    application = create_app()
    application.state.runtime_registry = runtimes

    async def workspace_repository():  # type: ignore[no-untyped-def]
        yield workspaces

    async def document_repository():  # type: ignore[no-untyped-def]
        yield documents

    application.dependency_overrides[get_workspace_repository] = workspace_repository
    application.dependency_overrides[get_document_repository] = document_repository

    async def exercise():  # type: ignore[no-untyped-def]
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            return await client.post(
                f"/v1/workspaces/{workspace_id}/query", json={"query": "question"}
            )

    response = asyncio.run(exercise())

    assert response.status_code == 502
    assert response.json() == {"detail": "query execution failed"}
    assert "private" not in response.text
    documents.list.assert_not_awaited()
