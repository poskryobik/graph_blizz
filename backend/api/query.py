"""Demo-owner Graph RAG query API."""

from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, StringConstraints

from backend.api.documents import get_document_repository
from backend.api.workspaces import _authorize, get_workspace_repository
from backend.documents import DocumentRepository
from backend.query import QueryResult, QueryService
from backend.rag import LightRAGRuntimeRegistry
from backend.security import Permission
from backend.workspaces import WorkspaceRepository

router = APIRouter(prefix="/v1/workspaces/{workspace_id}/query", tags=["query"])
QueryText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=10_000)
]


class QueryRequest(BaseModel):
    """Client-controlled query fields; physical namespaces are forbidden."""

    model_config = ConfigDict(extra="forbid")

    query: QueryText


class QuerySourceResponse(BaseModel):
    """Minimal public identity of an indexed document source."""

    document_id: UUID
    filename: str


class QueryResponse(BaseModel):
    """Public generated answer with workspace and request correlation."""

    answer: str
    workspace_id: UUID
    request_id: UUID
    sources: list[QuerySourceResponse]


async def get_query_service(
    request: Request,
    repository: Annotated[DocumentRepository, Depends(get_document_repository)],
) -> QueryService:
    """Build query orchestration from request-scoped metadata and app runtime."""
    return QueryService(
        repository,
        cast(LightRAGRuntimeRegistry, request.app.state.runtime_registry),
    )


@router.post("", response_model=QueryResponse)
async def query_workspace(
    workspace_id: UUID,
    payload: QueryRequest,
    request: Request,
    service: Annotated[QueryService, Depends(get_query_service)],
    workspace_repository: Annotated[
        WorkspaceRepository, Depends(get_workspace_repository)
    ],
) -> QueryResponse:
    """Generate an answer after Demo-owner workspace authorization."""
    workspace = await workspace_repository.get(workspace_id)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    context = await _authorize(request, workspace, Permission.QUERY_EXECUTE)
    try:
        result = await service.query(context, payload.query)
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="query execution failed",
        ) from error
    return _response(result)


def _response(result: QueryResult) -> QueryResponse:
    """Serialize only the public query result fields."""
    return QueryResponse.model_validate(result, from_attributes=True)
