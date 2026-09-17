"""Owner-only workspace HTTP API."""

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, cast
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, StringConstraints

from backend.config import PostgreSQLSettings
from backend.security import (
    AuthorizedWorkspaceContext,
    IdentityResolver,
    Permission,
    WorkspaceAccessPolicy,
)
from backend.workspaces import Workspace, WorkspaceRepository, WorkspaceStatus

router = APIRouter(prefix="/workspaces", tags=["workspaces"])
NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class WorkspaceCreate(BaseModel):
    """Client-controlled fields accepted when creating a workspace."""

    model_config = ConfigDict(extra="forbid")

    name: NonEmptyText
    slug: NonEmptyText


class WorkspaceResponse(BaseModel):
    """Public workspace metadata without its physical storage namespace."""

    id: UUID
    name: str
    slug: str
    status: WorkspaceStatus
    created_at: datetime
    updated_at: datetime


async def get_workspace_repository(
    request: Request,
) -> AsyncIterator[WorkspaceRepository]:
    """Provide a request-scoped repository with transaction lifecycle."""
    settings = cast(PostgreSQLSettings, request.app.state.settings.postgres)
    password = (
        settings.password.get_secret_value() if settings.password is not None else None
    )
    async with await psycopg.AsyncConnection.connect(
        host=settings.host,
        port=settings.port,
        dbname=settings.database,
        user=settings.username,
        password=password,
        sslmode=settings.ssl_mode,
    ) as connection:
        yield WorkspaceRepository(connection)


@router.post("", response_model=WorkspaceResponse, status_code=status.HTTP_201_CREATED)
async def create_workspace(
    payload: WorkspaceCreate,
    request: Request,
    repository: Annotated[WorkspaceRepository, Depends(get_workspace_repository)],
) -> WorkspaceResponse:
    """Create a workspace and authorize its owner-managed namespace."""
    workspace = await repository.create(name=payload.name, slug=payload.slug)
    await _authorize(request, workspace, Permission.WORKSPACE_MANAGE)
    return WorkspaceResponse.model_validate(workspace, from_attributes=True)


@router.get("/{workspace_id}", response_model=WorkspaceResponse)
async def get_workspace(
    workspace_id: UUID,
    request: Request,
    repository: Annotated[WorkspaceRepository, Depends(get_workspace_repository)],
) -> WorkspaceResponse:
    """Return a workspace after owner authorization through the security boundary."""
    workspace = await repository.get(workspace_id)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await _authorize(request, workspace, Permission.WORKSPACE_READ)
    return WorkspaceResponse.model_validate(workspace, from_attributes=True)


async def _authorize(
    request: Request,
    workspace: Workspace,
    permission: Permission,
) -> AuthorizedWorkspaceContext:
    """Resolve the request principal and require one workspace permission."""
    resolver = cast(IdentityResolver, request.app.state.identity_resolver)
    policy = cast(WorkspaceAccessPolicy, request.app.state.workspace_access_policy)
    principal = await resolver.resolve(request)
    try:
        context = await policy.authorize(principal, workspace, permission)
    except PermissionError as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN) from error
    if permission not in context.permissions:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    return context
