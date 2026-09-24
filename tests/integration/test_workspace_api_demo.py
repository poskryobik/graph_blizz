"""HTTP integration coverage for the DemoOwner workspace API."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from httpx2 import ASGITransport, AsyncClient, Response

from backend import create_app
from backend.api.workspaces import get_workspace_repository
from backend.config import ApplicationSettings, AuthMode
from backend.security import Permission
from backend.workspaces import Workspace, WorkspaceStatus

pytestmark = pytest.mark.integration

WORKSPACE_ID = UUID("12345678-1234-5678-1234-567812345678")
EARLIER_WORKSPACE_ID = UUID("87654321-4321-8765-4321-876543218765")
NOW = datetime(2026, 9, 17, 10, tzinfo=UTC)
EARLIER = datetime(2026, 9, 17, 9, tzinfo=UTC)
PUBLIC_FIELDS = {
    "id",
    "name",
    "slug",
    "description",
    "status",
    "created_at",
    "updated_at",
}


def test_demo_owner_creates_and_reads_workspace_without_credentials() -> None:
    application = create_app(
        ApplicationSettings(
            auth={"mode": AuthMode.DEMO_OWNER, "demo_owner_id": "api-owner"}
        )
    )
    workspace = Workspace(
        id=WORKSPACE_ID,
        name="Knowledge",
        slug="knowledge",
        storage_key="ws_server_generated",
        status=WorkspaceStatus.ACTIVE,
        created_at=NOW,
        updated_at=NOW,
        description="Architecture and source code",
    )
    repository = AsyncMock()
    repository.create.return_value = workspace
    repository.get.return_value = workspace

    async def override_repository():  # type: ignore[no-untyped-def]
        yield repository

    application.dependency_overrides[get_workspace_repository] = override_repository
    policy = application.state.workspace_access_policy
    policy.authorize = AsyncMock(wraps=policy.authorize)

    async def exercise() -> tuple[Response, Response]:
        transport = ASGITransport(app=application)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            created = await client.post(
                "/workspaces",
                json={
                    "name": "Knowledge",
                    "slug": "knowledge",
                    "description": "Architecture and source code",
                },
            )
            fetched = await client.get(f"/workspaces/{WORKSPACE_ID}")
            return created, fetched

    created, fetched = asyncio.run(exercise())

    assert created.status_code == 201
    assert fetched.status_code == 200
    assert created.json()["description"] == "Architecture and source code"
    assert fetched.json()["description"] == "Architecture and source code"
    assert "storage_key" not in created.json()
    assert "storage_key" not in fetched.json()
    forwarded = repository.create.await_args.kwargs["description"]
    assert forwarded == "Architecture and source code"
    assert [call.args[2] for call in policy.authorize.await_args_list] == [
        Permission.WORKSPACE_MANAGE,
        Permission.WORKSPACE_READ,
    ]
    assert all(
        call.args[0].principal_id == "api-owner"
        for call in policy.authorize.await_args_list
    )


def test_demo_owner_lists_all_workspaces_without_credentials() -> None:
    application = create_app(
        ApplicationSettings(
            auth={"mode": AuthMode.DEMO_OWNER, "demo_owner_id": "api-owner"}
        )
    )
    later = Workspace(
        id=WORKSPACE_ID,
        name="Knowledge",
        slug="knowledge",
        storage_key="ws_later_generated",
        status=WorkspaceStatus.ACTIVE,
        created_at=NOW,
        updated_at=NOW,
        description="Architecture and source code",
    )
    earlier = Workspace(
        id=EARLIER_WORKSPACE_ID,
        name="Earlier",
        slug="earlier",
        storage_key="ws_earlier_generated",
        status=WorkspaceStatus.ARCHIVED,
        created_at=EARLIER,
        updated_at=EARLIER,
    )
    repository = AsyncMock()
    repository.list.return_value = [earlier, later]

    async def override_repository():  # type: ignore[no-untyped-def]
        yield repository

    application.dependency_overrides[get_workspace_repository] = override_repository
    policy = application.state.workspace_access_policy
    policy.authorize = AsyncMock(wraps=policy.authorize)

    async def list_workspaces() -> Response:
        transport = ASGITransport(app=application)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            return await client.get("/workspaces")

    response = asyncio.run(list_workspaces())

    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [
        str(EARLIER_WORKSPACE_ID),
        str(WORKSPACE_ID),
    ]
    assert [item["status"] for item in response.json()] == ["ARCHIVED", "ACTIVE"]
    assert all(set(item) == PUBLIC_FIELDS for item in response.json())
    policy.authorize.assert_not_awaited()
