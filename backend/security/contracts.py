"""Application protocols for replaceable identity and authorization adapters."""

from typing import Protocol

from backend.security.models import (
    AuthorizedWorkspaceContext,
    Permission,
    PrincipalContext,
)
from backend.workspaces import Workspace


class IdentityResolver(Protocol):
    """Resolve a normalized principal from an incoming transport request."""

    async def resolve(self, request: object) -> PrincipalContext:
        """Return the principal represented by a request."""
        ...


class WorkspaceAccessPolicy(Protocol):
    """Authorize an operation and bind it to a server-resolved workspace."""

    async def authorize(
        self,
        principal: PrincipalContext,
        workspace: Workspace,
        permission: Permission,
    ) -> AuthorizedWorkspaceContext:
        """Return an authorized context or reject access."""
        ...
