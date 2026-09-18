"""Persistent document metadata models."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class DocumentStatus(StrEnum):
    """Demo document lifecycle states stored in PostgreSQL."""

    UPLOADED = "UPLOADED"
    INDEXING = "INDEXING"
    READY = "READY"
    UPDATING = "UPDATING"
    DELETING = "DELETING"
    DELETED = "DELETED"
    FAILED = "FAILED"


class DocumentUpsertAction(StrEnum):
    """Public outcome of an idempotent document upsert."""

    CREATED = "created"
    UPDATED = "updated"
    UNCHANGED = "unchanged"


@dataclass(frozen=True, slots=True)
class Document:
    """Persistent metadata for one workspace document source."""

    id: UUID
    workspace_id: UUID
    source_key: str
    filename: str
    source_type: str
    object_uri: str
    content_hash: str
    status: DocumentStatus
    created_at: datetime
    updated_at: datetime
    active_revision: int = 1


@dataclass(frozen=True, slots=True)
class DocumentRevision:
    """Immutable source metadata for one numbered document revision."""

    document_id: UUID
    revision: int
    object_uri: str
    content_hash: str
    created_at: datetime
