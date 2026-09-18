"""Demo-owner document upload and read API."""

from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import PureWindowsPath
from typing import Annotated, cast
from uuid import UUID, uuid4

import psycopg
from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel

from backend.api.workspaces import _authorize, get_workspace_repository
from backend.config import PostgreSQLSettings
from backend.documents import (
    Document,
    DocumentRepository,
    DocumentScopeConflictError,
    DocumentSourceService,
    DocumentStatus,
    DocumentUpsertAction,
)
from backend.jobs import JobRepository, JobStatus
from backend.parsers import ParserRegistry, UnsupportedDocumentTypeError
from backend.security import Permission
from backend.storage import ObjectStorageError, ObjectStore
from backend.workspaces import Workspace, WorkspaceRepository

router = APIRouter(prefix="/v1/workspaces/{workspace_id}/documents", tags=["documents"])
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


class DocumentResponse(BaseModel):
    """Public metadata that does not expose physical storage identifiers."""

    id: UUID
    workspace_id: UUID
    filename: str
    source_type: str
    status: DocumentStatus
    created_at: datetime
    updated_at: datetime


class DocumentUploadResponse(BaseModel):
    """Public create/no-op outcome without physical storage metadata."""

    action: DocumentUpsertAction
    id: UUID
    workspace_id: UUID
    filename: str
    source_type: str
    revision: int
    status: DocumentStatus | JobStatus
    job_id: UUID | None
    created_at: datetime
    updated_at: datetime


class DocumentDeleteResponse(BaseModel):
    """Public asynchronous or already-completed deletion outcome."""

    id: UUID
    workspace_id: UUID
    revision: int
    status: DocumentStatus | JobStatus
    job_id: UUID | None


async def get_document_repository(
    request: Request,
) -> AsyncIterator[DocumentRepository]:
    """Provide a request-scoped document repository and transaction."""
    settings = cast(PostgreSQLSettings, request.app.state.settings.postgres)
    password = settings.password.get_secret_value() if settings.password else None
    async with await psycopg.AsyncConnection.connect(
        host=settings.host,
        port=settings.port,
        dbname=settings.database,
        user=settings.username,
        password=password,
        sslmode=settings.ssl_mode,
    ) as connection:
        yield DocumentRepository(connection)


def get_source_service(
    request: Request,
    repository: Annotated[DocumentRepository, Depends(get_document_repository)],
) -> DocumentSourceService:
    """Build source persistence from request metadata and app-owned object store."""
    return DocumentSourceService(
        repository, cast(ObjectStore, request.app.state.object_store)
    )


def get_job_repository(
    repository: Annotated[DocumentRepository, Depends(get_document_repository)],
) -> JobRepository:
    """Build durable job persistence on the document transaction connection."""
    return JobRepository(repository.connection)


@router.post(
    "", response_model=DocumentUploadResponse, status_code=status.HTTP_201_CREATED
)
async def upload_document(
    workspace_id: UUID,
    request: Request,
    file: Annotated[UploadFile, File()],
    repository: Annotated[DocumentRepository, Depends(get_document_repository)],
    sources: Annotated[DocumentSourceService, Depends(get_source_service)],
    jobs: Annotated[JobRepository, Depends(get_job_repository)],
    workspace_repository: Annotated[
        WorkspaceRepository, Depends(get_workspace_repository)
    ],
) -> DocumentUploadResponse:
    """Persist a supported immutable source and enqueue durable indexing."""
    workspace = await _workspace_or_404(workspace_repository, workspace_id)
    await _authorize(request, workspace, Permission.DOCUMENT_CREATE)
    filename = _safe_filename(file.filename)
    source_type = file.content_type or "application/octet-stream"
    parsers = cast(ParserRegistry, request.app.state.parser_registry)
    try:
        parsers.get_parser(media_type=source_type, filename=filename)
    except UnsupportedDocumentTypeError as error:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
        ) from error

    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="empty upload"
        )
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
    try:
        document, job = await sources.store_for_indexing(
            jobs=jobs,
            workspace_id=workspace_id,
            source_key=f"upload-{uuid4()}",
            filename=filename,
            source_type=source_type,
            content=content,
        )
    except ObjectStorageError as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE) from error
    return DocumentUploadResponse(
        action=DocumentUpsertAction.CREATED,
        id=document.id,
        workspace_id=document.workspace_id,
        filename=document.filename,
        source_type=document.source_type,
        revision=document.active_revision,
        status=job.status,
        job_id=job.id,
        created_at=document.created_at,
        updated_at=document.updated_at,
    )


@router.put("/{document_id}", response_model=DocumentUploadResponse)
async def upsert_document(
    workspace_id: UUID,
    document_id: UUID,
    request: Request,
    file: Annotated[UploadFile, File()],
    repository: Annotated[DocumentRepository, Depends(get_document_repository)],
    sources: Annotated[DocumentSourceService, Depends(get_source_service)],
    jobs: Annotated[JobRepository, Depends(get_job_repository)],
    workspace_repository: Annotated[
        WorkspaceRepository, Depends(get_workspace_repository)
    ],
) -> DocumentUploadResponse:
    """Create a stable document or return its identical active revision."""
    workspace = await _workspace_or_404(workspace_repository, workspace_id)
    await _authorize(request, workspace, Permission.DOCUMENT_CREATE)
    filename = _safe_filename(file.filename)
    source_type = file.content_type or "application/octet-stream"
    parsers = cast(ParserRegistry, request.app.state.parser_registry)
    try:
        parsers.get_parser(media_type=source_type, filename=filename)
    except UnsupportedDocumentTypeError as error:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
        ) from error
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="empty upload"
        )
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
    try:
        result = await sources.upsert_for_indexing(
            jobs=jobs,
            workspace_id=workspace_id,
            document_id=document_id,
            source_key=f"upsert-{document_id}",
            filename=filename,
            source_type=source_type,
            content=content,
        )
    except DocumentScopeConflictError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from error
    except ObjectStorageError as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE) from error
    document = result.document
    return DocumentUploadResponse(
        action=result.action,
        id=document.id,
        workspace_id=document.workspace_id,
        filename=document.filename,
        source_type=document.source_type,
        revision=(
            document.active_revision
            if result.job is None
            else result.job.document_revision
        ),
        status=document.status if result.job is None else result.job.status,
        job_id=None if result.job is None else result.job.id,
        created_at=document.created_at,
        updated_at=document.updated_at,
    )


@router.get("", response_model=list[DocumentResponse])
async def list_documents(
    workspace_id: UUID,
    request: Request,
    repository: Annotated[DocumentRepository, Depends(get_document_repository)],
    workspace_repository: Annotated[
        WorkspaceRepository, Depends(get_workspace_repository)
    ],
) -> list[DocumentResponse]:
    """List public metadata for the authorized workspace only."""
    workspace = await _workspace_or_404(workspace_repository, workspace_id)
    await _authorize(request, workspace, Permission.DOCUMENT_READ)
    return [_response(document) for document in await repository.list(workspace_id)]


@router.delete("/{document_id}", response_model=DocumentDeleteResponse)
async def delete_document(
    workspace_id: UUID,
    document_id: UUID,
    request: Request,
    response: Response,
    sources: Annotated[DocumentSourceService, Depends(get_source_service)],
    jobs: Annotated[JobRepository, Depends(get_job_repository)],
    workspace_repository: Annotated[
        WorkspaceRepository, Depends(get_workspace_repository)
    ],
) -> DocumentDeleteResponse:
    """Authorize and durably enqueue deletion of one workspace document."""
    workspace = await _workspace_or_404(workspace_repository, workspace_id)
    await _authorize(request, workspace, Permission.DOCUMENT_DELETE)
    try:
        result = await sources.delete_for_indexing(
            jobs=jobs, workspace_id=workspace_id, document_id=document_id
        )
    except (DocumentScopeConflictError, LookupError) as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from error
    except RuntimeError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT) from error
    response.status_code = (
        status.HTTP_200_OK if result.job is None else status.HTTP_202_ACCEPTED
    )
    return DocumentDeleteResponse(
        id=result.document.id,
        workspace_id=result.document.workspace_id,
        revision=result.document.active_revision,
        status=result.document.status if result.job is None else result.job.status,
        job_id=None if result.job is None else result.job.id,
    )


@router.get("/{document_id}", response_model=DocumentResponse)
async def get_document(
    workspace_id: UUID,
    document_id: UUID,
    request: Request,
    repository: Annotated[DocumentRepository, Depends(get_document_repository)],
    workspace_repository: Annotated[
        WorkspaceRepository, Depends(get_workspace_repository)
    ],
) -> DocumentResponse:
    """Return one authorized workspace document without cross-scope fallback."""
    workspace = await _workspace_or_404(workspace_repository, workspace_id)
    await _authorize(request, workspace, Permission.DOCUMENT_READ)
    document = await repository.get(workspace_id, document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return _response(document)


async def _workspace_or_404(
    repository: WorkspaceRepository, workspace_id: UUID
) -> Workspace:
    """Resolve one server-side workspace without leaking absent scopes."""
    workspace = await repository.get(workspace_id)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return workspace


def _safe_filename(filename: str | None) -> str:
    """Accept a bounded basename and reject client-controlled paths."""
    if (
        not filename
        or filename in {".", ".."}
        or "/" in filename
        or "\\" in filename
        or PureWindowsPath(filename).drive
    ):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT)
    if "\x00" in filename or len(filename) > 255:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT)
    return filename


def _response(document: Document) -> DocumentResponse:
    """Serialize only public document metadata."""
    return DocumentResponse.model_validate(document, from_attributes=True)
