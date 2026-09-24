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
NOW = datetime(2026, 9, 17, 10, tzinfo=UTC)


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
