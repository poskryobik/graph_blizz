"""Unit coverage for asynchronous public document upload."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient

from backend import create_app
from backend.api.documents import (
    get_document_repository,
    get_job_repository,
    get_source_service,
)
from backend.api.workspaces import get_workspace_repository
from backend.documents import Document, DocumentStatus
from backend.jobs import Job, JobStatus, JobType
from backend.workspaces import Workspace, WorkspaceStatus

pytestmark = pytest.mark.unit
WORKSPACE_ID = UUID("12345678-1234-5678-1234-567812345678")
DOCUMENT_ID = UUID("aaaaaaaa-1234-5678-1234-567812345678")
JOB_ID = UUID("bbbbbbbb-1234-5678-1234-567812345678")
NOW = datetime(2026, 9, 18, 10, tzinfo=UTC)


def test_upload_returns_pending_job_without_inline_indexing() -> None:
    workspace = Workspace(
        id=WORKSPACE_ID,
        name="Knowledge",
        slug="knowledge",
        storage_key="server-only",
        status=WorkspaceStatus.ACTIVE,
        created_at=NOW,
        updated_at=NOW,
    )
    document = Document(
        id=DOCUMENT_ID,
        workspace_id=WORKSPACE_ID,
        source_key="server-only",
        filename="notes.md",
        source_type="text/markdown",
        object_uri="s3://private/source",
        content_hash="private-hash",
        status=DocumentStatus.UPLOADED,
        created_at=NOW,
        updated_at=NOW,
        active_revision=None,
    )
    job = Job(
        id=JOB_ID,
        document_id=DOCUMENT_ID,
        document_revision=1,
        type=JobType.INDEX_DOCUMENT,
        status=JobStatus.PENDING,
        attempts=0,
        max_attempts=3,
        available_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )
    workspaces = AsyncMock()
    workspaces.get.return_value = workspace
    documents = AsyncMock()
    sources = AsyncMock()
    sources.store_for_indexing.return_value = (document, job)
    jobs = AsyncMock()
    app = create_app()

    async def workspace_repository():  # type: ignore[no-untyped-def]
        yield workspaces

    async def document_repository():  # type: ignore[no-untyped-def]
        yield documents

    async def source_service():  # type: ignore[no-untyped-def]
        return sources

    async def job_repository():  # type: ignore[no-untyped-def]
        return jobs

    app.dependency_overrides[get_workspace_repository] = workspace_repository
    app.dependency_overrides[get_document_repository] = document_repository
    app.dependency_overrides[get_source_service] = source_service
    app.dependency_overrides[get_job_repository] = job_repository

    async def send():  # type: ignore[no-untyped-def]
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            return await client.post(
                f"/v1/workspaces/{WORKSPACE_ID}/documents",
                files={"file": ("notes.md", b"# Notes", "text/markdown")},
            )

    response = asyncio.run(send())

    assert response.status_code == 201
    assert response.json()["status"] == "PENDING"
    assert response.json()["job_id"] == str(JOB_ID)
    assert response.json()["revision"] == 1
    assert {"source_key", "object_uri", "content_hash"}.isdisjoint(response.json())
    sources.store_for_indexing.assert_awaited_once()
    assert sources.store_for_indexing.await_args.kwargs["jobs"] is jobs
