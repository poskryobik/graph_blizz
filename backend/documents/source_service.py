"""Original document source persistence orchestration."""

from collections.abc import Callable
from hashlib import sha256
from uuid import UUID, uuid4

from backend.documents.models import Document, DocumentRevision
from backend.documents.repository import DocumentRepository
from backend.storage import ObjectStorageError, ObjectStore


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
        object_key = self.object_key(workspace_id, document_id, 1)
        self._object_store.put(object_key, content)
        try:
            document = await self._repository.create(
                document_id=document_id,
                workspace_id=workspace_id,
                source_key=source_key,
                filename=filename,
                source_type=source_type,
                object_uri=self._object_store.uri(object_key),
                content_hash=sha256(content).hexdigest(),
            )
            await self._repository.commit()
            return document
        except BaseException:
            await self._rollback()
            try:
                self._object_store.delete(object_key)
            except ObjectStorageError:
                pass
            raise

    async def _rollback(self) -> None:
        """Best-effort rollback after metadata persistence fails."""
        try:
            await self._repository.rollback()
        except BaseException:  # noqa: BLE001 - preserve the persistence failure
            return

    def read(self, document: Document) -> bytes:
        """Read the exact source bytes persisted for ``document``."""
        return self._object_store.get_uri(document.object_uri)

    async def add_revision(
        self, *, document: Document, content: bytes
    ) -> DocumentRevision:
        """Persist and register the next immutable source revision."""
        stored_key: str | None = None

        def store_source(revision: int) -> str:
            nonlocal stored_key
            stored_key = self.object_key(document.workspace_id, document.id, revision)
            self._object_store.put(stored_key, content)
            return self._object_store.uri(stored_key)

        try:
            result = await self._repository.add_revision(
                workspace_id=document.workspace_id,
                document_id=document.id,
                content_hash=sha256(content).hexdigest(),
                object_uri_factory=store_source,
            )
            if result is None:
                raise LookupError("document does not exist")
            await self._repository.commit()
            return result
        except BaseException:
            await self._rollback()
            if stored_key is not None:
                try:
                    self._object_store.delete(stored_key)
                except ObjectStorageError:
                    pass
            raise

    @staticmethod
    def object_key(workspace_id: UUID, document_id: UUID, revision: int = 1) -> str:
        """Return the immutable key for a numbered document revision."""
        if revision < 1:
            raise ValueError("revision must be positive")
        return (
            f"workspace/{workspace_id}/document/{document_id}/"
            f"revision/{revision}/source"
        )
