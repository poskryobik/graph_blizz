"""Owner-only workspace HTTP API."""

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, cast
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, StringConstraints, field_validator

from backend.config import ApplicationSettings, PostgreSQLSettings
from backend.index_versions import active_index_contract
from backend.jobs import JobRepository, JobStatus
from backend.maintenance import WorkspaceReindexService
from backend.security import (
    AuthorizedWorkspaceContext,
    IdentityResolver,
    Permission,
    WorkspaceAccessPolicy,
)
from backend.workspaces import Workspace, WorkspaceRepository, WorkspaceStatus

router = APIRouter(prefix="/workspaces", tags=["workspaces"])
NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
DescriptionText = Annotated[
    str, StringConstraints(strip_whitespace=True, max_length=2000)
]


class WorkspaceCreate(BaseModel):
    """Client-controlled fields accepted when creating a workspace."""

    model_config = ConfigDict(extra="forbid")

    name: NonEmptyText
    slug: NonEmptyText
    description: DescriptionText | None = None

    @field_validator("description")
    @classmethod
    def _blank_description_is_absent(cls, value: str | None) -> str | None:
        """Treat a whitespace-only description as no description."""
        return value or None


class WorkspaceResponse(BaseModel):
    """Public workspace metadata without its physical storage namespace."""

    id: UUID
    name: str
    slug: str
    description: str | None = None
    status: WorkspaceStatus
    created_at: datetime
    updated_at: datetime


class WorkspaceReindexResponse(BaseModel):
    """Durable jobs created by one workspace maintenance request."""

    workspace_id: UUID
    status: JobStatus
    job_ids: list[UUID]


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


async def get_reindex_service(
    request: Request,
) -> AsyncIterator[WorkspaceReindexService]:
    """Provide maintenance persistence with a request-scoped transaction."""
    application_settings = cast(ApplicationSettings, request.app.state.settings)
    settings = application_settings.postgres
    password = settings.password.get_secret_value() if settings.password else None
    async with await psycopg.AsyncConnection.connect(
        host=settings.host,
        port=settings.port,
        dbname=settings.database,
        user=settings.username,
        password=password,
        sslmode=settings.ssl_mode,
    ) as connection:
        yield WorkspaceReindexService(
            WorkspaceRepository(connection),
            JobRepository(connection),
            active_index_contract(application_settings.embedding),
        )


@router.post("", response_model=WorkspaceResponse, status_code=status.HTTP_201_CREATED)
async def create_workspace(
    payload: WorkspaceCreate,
    request: Request,
    repository: Annotated[WorkspaceRepository, Depends(get_workspace_repository)],
) -> WorkspaceResponse:
    """Create a workspace and authorize its owner-managed namespace."""
    workspace = await repository.create(
        name=payload.name,
        slug=payload.slug,
        description=payload.description,
    )
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


@router.get("", response_model=list[WorkspaceResponse])
async def list_workspaces(
    repository: Annotated[WorkspaceRepository, Depends(get_workspace_repository)],
) -> list[WorkspaceResponse]:
    """List every workspace without consulting a LightRAG runtime.

    Listing spans workspaces, so the workspace-scoped access policy has no
    concrete workspace to authorize; DemoOwner mode exposes the owner's
    workspaces directly.
    """
    return [
        WorkspaceResponse.model_validate(workspace, from_attributes=True)
        for workspace in await repository.list()
    ]


@router.post(
    "/{workspace_id}/maintenance/reindex",
    response_model=WorkspaceReindexResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def reindex_workspace(
    workspace_id: UUID,
    request: Request,
    repository: Annotated[WorkspaceRepository, Depends(get_workspace_repository)],
    service: Annotated[WorkspaceReindexService, Depends(get_reindex_service)],
) -> WorkspaceReindexResponse:
    """Authorize, reconcile the contract, and enqueue affected active revisions."""
    workspace = await repository.get(workspace_id)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await _authorize(request, workspace, Permission.INDEX_REBUILD)
    jobs = await service.enqueue(workspace_id)
    return WorkspaceReindexResponse(
        workspace_id=workspace_id,
        status=JobStatus.PENDING,
        job_ids=[job.id for job in jobs],
    )


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
