"""Persistent document registry."""

from backend.documents.models import (
    Document,
    DocumentRevision,
    DocumentStatus,
    DocumentUpsertAction,
)
from backend.documents.repository import DocumentRepository, DocumentScopeConflictError
from backend.documents.source_service import (
    DocumentSourceService,
    DocumentUpsertResult,
)

__all__ = [
    "Document",
    "DocumentRepository",
    "DocumentRevision",
    "DocumentScopeConflictError",
    "DocumentSourceService",
    "DocumentStatus",
    "DocumentUpsertAction",
    "DocumentUpsertResult",
]
