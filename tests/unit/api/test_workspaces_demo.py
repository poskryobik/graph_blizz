"""Unit coverage for workspace API authorization and public schemas."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from httpx2 import ASGITransport, AsyncClient, Response

from backend import create_app
from backend.api.workspaces import get_workspace_repository
from backend.security import AuthorizedWorkspaceContext, PrincipalType
from backend.workspaces import Workspace, WorkspaceStatus

pytestmark = pytest.mark.unit

WORKSPACE_ID = UUID("12345678-1234-5678-1234-567812345678")
NOW = datetime(2026, 9, 17, 10, tzinfo=UTC)
WORKSPACE = Workspace(
    id=WORKSPACE_ID,
    name="Knowledge",
    slug="knowledge",
    storage_key="ws_server_generated",
    status=WorkspaceStatus.ACTIVE,
    created_at=NOW,
    updated_at=NOW,
)


def _request(method: str, path: str, *, json: dict[str, str] | None = None) -> Response:
    application = create_app()
    repository = AsyncMock()
    repository.create.return_value = WORKSPACE
    repository.get.return_value = WORKSPACE

    async def override_repository():  # type: ignore[no-untyped-def]
        yield repository

    application.dependency_overrides[get_workspace_repository] = override_repository

    async def send() -> Response:
        transport = ASGITransport(app=application)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            return await client.request(method, path, json=json)

    return asyncio.run(send())


def test_create_accepts_only_public_fields_and_hides_storage_key() -> None:
    response = _request(
        "POST",
        "/workspaces",
        json={"name": "Knowledge", "slug": "knowledge"},
    )

    assert response.status_code == 201
    assert response.json() == {
        "id": str(WORKSPACE_ID),
        "name": "Knowledge",
        "slug": "knowledge",
        "status": "ACTIVE",
        "created_at": "2026-09-17T10:00:00Z",
        "updated_at": "2026-09-17T10:00:00Z",
    }


def test_create_rejects_client_supplied_storage_key() -> None:
    response = _request(
        "POST",
        "/workspaces",
        json={
            "name": "Knowledge",
            "slug": "knowledge",
            "storage_key": "client-selected",
        },
    )

    assert response.status_code == 422


def test_missing_workspace_returns_not_found() -> None:
    application = create_app()
    repository = AsyncMock()
    repository.get.return_value = None

    async def override_repository():  # type: ignore[no-untyped-def]
        yield repository

    application.dependency_overrides[get_workspace_repository] = override_repository

    async def get() -> Response:
        transport = ASGITransport(app=application)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            return await client.get(f"/workspaces/{WORKSPACE_ID}")

    response = asyncio.run(get())
    assert response.status_code == 404


def test_missing_required_permission_is_forbidden() -> None:
    application = create_app()
    repository = AsyncMock()
    repository.get.return_value = WORKSPACE

    async def override_repository():  # type: ignore[no-untyped-def]
        yield repository

    application.dependency_overrides[get_workspace_repository] = override_repository
    application.state.workspace_access_policy.authorize = AsyncMock(
        return_value=AuthorizedWorkspaceContext(
            principal_id="demo-owner",
            principal_type=PrincipalType.USER,
            workspace_id=WORKSPACE_ID,
            storage_key=WORKSPACE.storage_key,
            permissions=frozenset(),
        )
    )

    async def get() -> Response:
        transport = ASGITransport(app=application)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            return await client.get(f"/workspaces/{WORKSPACE_ID}")

    response = asyncio.run(get())
    assert response.status_code == 403
