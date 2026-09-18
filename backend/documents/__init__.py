"""Persistent document registry."""

from backend.documents.models import (
    Document,
    DocumentRevision,
    DocumentStatus,
    DocumentUpsertAction,
)
from backend.documents.repository import DocumentRepository, DocumentScopeConflictError
from backend.documents.source_service import (
    DocumentDeleteResult,
    DocumentSourceService,
    DocumentUpsertResult,
)

__all__ = [
    "Document",
    "DocumentDeleteResult",
    "DocumentRepository",
    "DocumentRevision",
    "DocumentScopeConflictError",
    "DocumentSourceService",
    "DocumentStatus",
    "DocumentUpsertAction",
    "DocumentUpsertResult",
]
