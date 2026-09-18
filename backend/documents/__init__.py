"""Persistent document registry."""

from backend.documents.models import (
    Document,
    DocumentRevision,
    DocumentStatus,
    DocumentUpsertAction,
)
from backend.documents.repository import DocumentRepository, DocumentScopeConflictError
from backend.documents.source_service import (
    DocumentContentChangedError,
    DocumentSourceService,
    DocumentUpsertResult,
)

__all__ = [
    "Document",
    "DocumentContentChangedError",
    "DocumentRepository",
    "DocumentRevision",
    "DocumentScopeConflictError",
    "DocumentSourceService",
    "DocumentStatus",
    "DocumentUpsertAction",
    "DocumentUpsertResult",
]
