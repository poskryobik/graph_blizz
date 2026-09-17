"""Unit coverage for the credential-free DemoOwner security adapters."""

import asyncio
from datetime import UTC, datetime
from uuid import UUID

import pytest

from backend.security import (
    ALL_OWNER_PERMISSIONS,
    DemoOwnerAccessPolicy,
    DemoOwnerIdentityResolver,
    Permission,
    PrincipalContext,
    PrincipalType,
)
from backend.workspaces import Workspace, WorkspaceStatus

pytestmark = pytest.mark.unit

WORKSPACE_ID = UUID("12345678-1234-5678-1234-567812345678")


def _workspace() -> Workspace:
    timestamp = datetime(2026, 9, 17, tzinfo=UTC)
    return Workspace(
        id=WORKSPACE_ID,
        name="Knowledge",
        slug="knowledge",
        storage_key="ws_0123456789abcdef0123456789abcdef",
        status=WorkspaceStatus.ACTIVE,
        created_at=timestamp,
        updated_at=timestamp,
    )


def test_identity_resolver_returns_configured_owner_without_credentials() -> None:
    resolver = DemoOwnerIdentityResolver("test-owner")

    principal = asyncio.run(resolver.resolve(object()))

    assert principal == PrincipalContext(
        principal_id="test-owner",
        principal_type=PrincipalType.USER,
    )


@pytest.mark.parametrize("permission", list(Permission))
def test_owner_receives_all_demo_mvp_permissions(permission: Permission) -> None:
    principal = asyncio.run(DemoOwnerIdentityResolver("test-owner").resolve(object()))

    context = asyncio.run(
        DemoOwnerAccessPolicy("test-owner").authorize(
            principal,
            _workspace(),
            permission,
        )
    )

    assert context.workspace_id == WORKSPACE_ID
    assert context.storage_key == "ws_0123456789abcdef0123456789abcdef"
    assert context.permissions == ALL_OWNER_PERMISSIONS == frozenset(Permission)


def test_access_policy_rejects_a_principal_other_than_configured_owner() -> None:
    principal = PrincipalContext("someone-else", PrincipalType.USER)

    with pytest.raises(PermissionError, match="configured demo owner"):
        asyncio.run(
            DemoOwnerAccessPolicy("test-owner").authorize(
                principal,
                _workspace(),
                Permission.WORKSPACE_READ,
            )
        )
