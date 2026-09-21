"""Credential-free security adapters for the single-owner Demo/MVP mode."""

from backend.security.models import (
    ALL_OWNER_PERMISSIONS,
    AuthorizedWorkspaceContext,
    Permission,
    PrincipalContext,
    PrincipalType,
)
from backend.workspaces import Workspace


class DemoOwnerIdentityResolver:
    """Resolve every request to the configured Demo/MVP bootstrap owner."""

    def __init__(self, owner_id: str = "demo-owner") -> None:
        """Configure the stable technical identity used by Demo/MVP requests."""
        self._principal = PrincipalContext(
            principal_id=owner_id,
            principal_type=PrincipalType.USER,
        )

    async def resolve(self, request: object) -> PrincipalContext:
        """Return the demo owner without inspecting headers or contacting an IdP."""
        del request
        return self._principal


class DemoOwnerAccessPolicy:
    """Grant the configured demo owner every Demo/MVP workspace permission."""

    def __init__(self, owner_id: str = "demo-owner") -> None:
        """Restrict this policy to the configured bootstrap owner identity."""
        self._owner_id = owner_id

    async def authorize(
        self,
        principal: PrincipalContext,
        workspace: Workspace,
        permission: Permission,
    ) -> AuthorizedWorkspaceContext:
        """Authorize the owner and bind access to trusted workspace metadata."""
        if (
            principal.principal_id != self._owner_id
            or principal.principal_type is not PrincipalType.USER
        ):
            raise PermissionError("principal is not the configured demo owner")

        if permission not in ALL_OWNER_PERMISSIONS:
            raise PermissionError("permission is not available in demo owner mode")

        return AuthorizedWorkspaceContext(
            principal_id=principal.principal_id,
            principal_type=principal.principal_type,
            workspace_id=workspace.id,
            storage_key=workspace.storage_key,
            permissions=ALL_OWNER_PERMISSIONS,
            index_schema_version=workspace.index_schema_version,
            embedding_profile=workspace.embedding_profile,
        )
