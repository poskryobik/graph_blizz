"""Persistent document registry."""

from backend.documents.models import Document, DocumentRevision, DocumentStatus
from backend.documents.repository import DocumentRepository
from backend.documents.source_service import DocumentSourceService

__all__ = [
    "Document",
    "DocumentRepository",
    "DocumentRevision",
    "DocumentSourceService",
    "DocumentStatus",
]
