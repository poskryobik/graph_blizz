"""Original document source persistence orchestration."""

from collections.abc import Callable
from hashlib import sha256
from uuid import UUID, uuid4

from backend.documents.models import Document
from backend.documents.repository import DocumentRepository
from backend.storage import ObjectStore


class DocumentSourceService:
    """Persist source bytes before registering their document metadata."""

    def __init__(
        self,
        repository: DocumentRepository,
        object_store: ObjectStore,
        *,
        id_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        """Bind document metadata and object storage boundaries."""
        self._repository = repository
        self._object_store = object_store
        self._id_factory = id_factory

    async def store(
        self,
        *,
        workspace_id: UUID,
        source_key: str,
        filename: str,
        source_type: str,
        content: bytes,
    ) -> Document:
        """Persist exact source bytes, then create their uploaded metadata."""
        document_id = self._id_factory()
        object_key = self.object_key(workspace_id, document_id)
        self._object_store.put(object_key, content)
        return await self._repository.create(
            document_id=document_id,
            workspace_id=workspace_id,
            source_key=source_key,
            filename=filename,
            source_type=source_type,
            object_uri=self._object_store.uri(object_key),
            content_hash=sha256(content).hexdigest(),
        )

    @staticmethod
    def object_key(workspace_id: UUID, document_id: UUID) -> str:
        """Return the revision-free Demo key for one workspace document."""
        return f"workspace/{workspace_id}/document/{document_id}/source"
