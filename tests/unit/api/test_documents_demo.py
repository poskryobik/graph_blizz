"""Unit coverage for the Demo document HTTP boundary."""

import asyncio
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient, Response

from backend import create_app
from backend.api.documents import (
    _safe_filename,
    get_document_repository,
    get_indexing_service,
    get_source_service,
)
from backend.api.workspaces import get_workspace_repository
from backend.documents import Document, DocumentStatus
from backend.security import Permission
from backend.workspaces import Workspace, WorkspaceStatus

pytestmark = pytest.mark.unit
WORKSPACE_ID = UUID("12345678-1234-5678-1234-567812345678")
DOCUMENT_ID = UUID("aaaaaaaa-1234-5678-1234-567812345678")
NOW = datetime(2026, 9, 18, 10, tzinfo=UTC)
WORKSPACE = Workspace(
    id=WORKSPACE_ID,
    name="Knowledge",
    slug="knowledge",
    storage_key="ws_server_generated",
    status=WorkspaceStatus.ACTIVE,
    created_at=NOW,
    updated_at=NOW,
)
UPLOADED = Document(
    id=DOCUMENT_ID,
    workspace_id=WORKSPACE_ID,
    source_key="server-only",
    filename="notes.md",
    source_type="text/markdown",
    object_uri="s3://private/server-only",
    content_hash="secret-hash",
    status=DocumentStatus.UPLOADED,
    created_at=NOW,
    updated_at=NOW,
)
READY = replace(UPLOADED, status=DocumentStatus.READY)


def _request(
    method: str,
    path: str,
    *,
    configure: Callable[[dict[str, AsyncMock]], None] | None = None,
    **kwargs: object,
) -> tuple[Response, dict[str, AsyncMock]]:
    app = create_app()
    dependencies = {
        "workspaces": AsyncMock(),
        "documents": AsyncMock(),
        "sources": AsyncMock(),
        "indexing": AsyncMock(),
    }
    dependencies["workspaces"].get.return_value = WORKSPACE
    dependencies["documents"].list.return_value = [READY]
    dependencies["documents"].get.return_value = READY
    dependencies["sources"].store.return_value = UPLOADED
    dependencies["indexing"].index.return_value = READY
    if configure is not None:
        configure(dependencies)

    async def workspace_repository():  # type: ignore[no-untyped-def]
        yield dependencies["workspaces"]

    async def document_repository():  # type: ignore[no-untyped-def]
        yield dependencies["documents"]

    async def source_service():  # type: ignore[no-untyped-def]
        return dependencies["sources"]

    async def indexing_service():  # type: ignore[no-untyped-def]
        return dependencies["indexing"]

    app.dependency_overrides[get_workspace_repository] = workspace_repository
    app.dependency_overrides[get_document_repository] = document_repository
    app.dependency_overrides[get_source_service] = source_service
    app.dependency_overrides[get_indexing_service] = indexing_service

    async def send() -> Response:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(send()), dependencies


def test_upload_indexes_inline_and_hides_server_storage_fields() -> None:
    response, dependencies = _request(
        "POST",
        f"/v1/workspaces/{WORKSPACE_ID}/documents",
        files={"file": ("notes.md", b"# Notes", "text/markdown")},
    )

    assert response.status_code == 201
    assert response.json()["status"] == "READY"
    assert {"source_key", "object_uri", "content_hash"}.isdisjoint(response.json())
    store_kwargs = dependencies["sources"].store.await_args.kwargs
    assert store_kwargs["filename"] == "notes.md"
    assert store_kwargs["content"] == b"# Notes"
    assert store_kwargs["source_key"].startswith("upload-")
    context = dependencies["indexing"].index.await_args.args[0]
    assert context.workspace_id == WORKSPACE_ID


def test_upload_rejects_paths_and_unsupported_types_before_storage() -> None:
    path_response, path_dependencies = _request(
        "POST",
        f"/v1/workspaces/{WORKSPACE_ID}/documents",
        files={"file": ("../notes.md", b"text", "text/markdown")},
    )
    type_response, type_dependencies = _request(
        "POST",
        f"/v1/workspaces/{WORKSPACE_ID}/documents",
        files={"file": ("notes.pdf", b"pdf", "application/pdf")},
    )

    assert path_response.status_code == 422
    assert type_response.status_code == 415
    path_dependencies["sources"].store.assert_not_awaited()
    type_dependencies["sources"].store.assert_not_awaited()


@pytest.mark.parametrize(
    "filename",
    ["..\\notes.md", "C:\\temp\\notes.md", "C:notes.md", "\\\\host\\share\\notes.md"],
)
def test_upload_rejects_windows_and_unc_paths(filename: str) -> None:
    with pytest.raises(HTTPException) as caught:
        _safe_filename(filename)

    assert caught.value.status_code == 422


def test_list_and_get_are_workspace_scoped_and_public() -> None:
    listed, dependencies = _request("GET", f"/v1/workspaces/{WORKSPACE_ID}/documents")
    fetched, fetched_dependencies = _request(
        "GET", f"/v1/workspaces/{WORKSPACE_ID}/documents/{DOCUMENT_ID}"
    )

    assert listed.status_code == fetched.status_code == 200
    assert listed.json() == [fetched.json()]
    dependencies["documents"].list.assert_awaited_once_with(WORKSPACE_ID)
    fetched_dependencies["documents"].get.assert_awaited_once_with(
        WORKSPACE_ID, DOCUMENT_ID
    )
    assert "object_uri" not in fetched.json()


def test_missing_workspace_and_document_are_not_found() -> None:
    missing_workspace, _ = _request(
        "GET",
        f"/v1/workspaces/{WORKSPACE_ID}/documents",
        configure=lambda dependencies: setattr(
            dependencies["workspaces"].get, "return_value", None
        ),
    )
    missing_document, _ = _request(
        "GET",
        f"/v1/workspaces/{WORKSPACE_ID}/documents/{UUID(int=0)}",
        configure=lambda dependencies: setattr(
            dependencies["documents"].get, "return_value", None
        ),
    )

    assert missing_workspace.status_code == 404
    assert missing_document.status_code == 404


def test_indexing_failure_returns_gateway_error_after_lifecycle_service_runs() -> None:
    app = create_app()
    workspaces = AsyncMock()
    workspaces.get.return_value = WORKSPACE
    sources = AsyncMock()
    sources.store.return_value = UPLOADED
    indexing = AsyncMock()
    indexing.index.side_effect = RuntimeError("private failure")

    async def workspace_repository():  # type: ignore[no-untyped-def]
        yield workspaces

    async def document_repository():  # type: ignore[no-untyped-def]
        yield AsyncMock()

    async def source_service():  # type: ignore[no-untyped-def]
        return sources

    async def indexing_service():  # type: ignore[no-untyped-def]
        return indexing

    app.dependency_overrides[get_workspace_repository] = workspace_repository
    app.dependency_overrides[get_document_repository] = document_repository
    app.dependency_overrides[get_source_service] = source_service
    app.dependency_overrides[get_indexing_service] = indexing_service

    async def send() -> Response:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            return await client.post(
                f"/v1/workspaces/{WORKSPACE_ID}/documents",
                files={"file": ("notes.txt", b"notes", "text/plain")},
            )

    response = asyncio.run(send())
    assert response.status_code == 502
    assert response.json() == {"detail": "document indexing failed"}
    indexing.index.assert_awaited_once()


def test_document_operations_request_document_permissions() -> None:
    response, _ = _request("GET", f"/v1/workspaces/{WORKSPACE_ID}/documents")
    assert response.status_code == 200
    # DemoOwner grants this permission; its explicit use is also covered by policy unit tests.
    assert Permission.DOCUMENT_READ.value == "document.read"
