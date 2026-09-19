"""Application-level integration coverage for Demo document upload."""

import asyncio
from dataclasses import replace
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

pytestmark = pytest.mark.integration


def test_demo_owner_uploads_pending_document_without_credentials() -> None:
    """Exercise routing, DemoOwner authorization and durable enqueue together."""
    workspace_id = UUID("12345678-1234-5678-1234-567812345678")
    document_id = UUID("aaaaaaaa-1234-5678-1234-567812345678")
    now = datetime(2026, 9, 18, 10, tzinfo=UTC)
    workspace = Workspace(
        id=workspace_id,
        name="Knowledge",
        slug="knowledge",
        storage_key="ws_server_generated",
        status=WorkspaceStatus.ACTIVE,
        created_at=now,
        updated_at=now,
    )
    uploaded = Document(
        id=document_id,
        workspace_id=workspace_id,
        source_key="server-generated",
        filename="guide.txt",
        source_type="text/plain",
        object_uri="s3://private/source",
        content_hash="private-hash",
        status=DocumentStatus.UPLOADED,
        created_at=now,
        updated_at=now,
        active_revision=None,
    )
    ready = replace(uploaded, status=DocumentStatus.READY)
    job = Job(
        id=UUID("bbbbbbbb-1234-5678-1234-567812345678"),
        document_id=document_id,
        document_revision=1,
        type=JobType.INDEX_DOCUMENT,
        status=JobStatus.PENDING,
        attempts=0,
        max_attempts=3,
        available_at=now,
        created_at=now,
        updated_at=now,
    )
    workspace_repository = AsyncMock()
    workspace_repository.get.return_value = workspace
    document_repository = AsyncMock()
    document_repository.list.return_value = [ready]
    source_service = AsyncMock()
    source_service.store_for_indexing.return_value = (uploaded, job)
    job_repository = AsyncMock()
    application = create_app()

    async def workspaces():  # type: ignore[no-untyped-def]
        yield workspace_repository

    async def documents():  # type: ignore[no-untyped-def]
        yield document_repository

    async def sources():  # type: ignore[no-untyped-def]
        return source_service

    async def jobs():  # type: ignore[no-untyped-def]
        return job_repository

    application.dependency_overrides[get_workspace_repository] = workspaces
    application.dependency_overrides[get_document_repository] = documents
    application.dependency_overrides[get_source_service] = sources
    application.dependency_overrides[get_job_repository] = jobs

    async def exercise() -> tuple[dict[str, object], list[dict[str, object]]]:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            created = await client.post(
                f"/v1/workspaces/{workspace_id}/documents",
                files={"file": ("guide.txt", b"Demo guide", "text/plain")},
            )
            listed = await client.get(f"/v1/workspaces/{workspace_id}/documents")
        assert created.status_code == 201
        assert listed.status_code == 200
        return created.json(), listed.json()

    created, listed = asyncio.run(exercise())
    assert created["status"] == "PENDING"
    assert created["job_id"] == str(job.id)
    assert listed[0]["status"] == "READY"
    assert "source_key" not in created
    source_service.store_for_indexing.assert_awaited_once()
