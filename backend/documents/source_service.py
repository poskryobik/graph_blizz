"""Original document source persistence orchestration."""

from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from uuid import UUID, uuid4

from backend.documents.models import (
    Document,
    DocumentRevision,
    DocumentStatus,
    DocumentUpsertAction,
)
from backend.documents.repository import DocumentRepository
from backend.jobs import Job, JobRepository, JobType
from backend.storage import ObjectStorageError, ObjectStore


@dataclass(frozen=True, slots=True)
class DocumentUpsertResult:
    """Document upsert outcome and an optional newly-created indexing job."""

    action: DocumentUpsertAction
    document: Document
    job: Job | None


@dataclass(frozen=True, slots=True)
class DocumentDeleteResult:
    """Idempotent deletion request outcome and its active job, if any."""

    document: Document
    job: Job | None


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
                activate=True,
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

    async def delete_for_indexing(
        self,
        *,
        jobs: JobRepository,
        workspace_id: UUID,
        document_id: UUID,
    ) -> DocumentDeleteResult:
        """Atomically enqueue deletion of a ready document's active revision.

        Repeated calls while deletion is active return the existing mutation job;
        already-deleted documents return a stable result without creating work.
        """
        try:
            document = await self._repository.lock_for_upsert(
                workspace_id=workspace_id, document_id=document_id
            )
            if document is None:
                raise LookupError("document does not exist")
            if document.status is DocumentStatus.DELETED:
                await self._repository.commit()
                return DocumentDeleteResult(document=document, job=None)
            if document.status is DocumentStatus.DELETING:
                job = await jobs.get_active_for_document(document_id)
                if job is None or job.type is not JobType.DELETE_DOCUMENT:
                    raise RuntimeError("deleting document has no active deletion job")
                await self._repository.commit()
                return DocumentDeleteResult(document=document, job=job)
            if document.active_revision is None:
                raise RuntimeError("document has no active revision to delete")
            deleting = await self._repository.transition_status(
                workspace_id=workspace_id,
                document_id=document_id,
                from_status=DocumentStatus.READY,
                to_status=DocumentStatus.DELETING,
            )
            if deleting is None:
                raise RuntimeError("document is not ready for deletion")
            job = await jobs.create_deletion(
                document_id=document_id,
                document_revision=document.active_revision,
            )
            await self._repository.commit()
            return DocumentDeleteResult(document=deleting, job=job)
        except BaseException:
            await self._rollback()
            raise

    async def store_for_indexing(
        self,
        *,
        jobs: JobRepository,
        workspace_id: UUID,
        source_key: str,
        filename: str,
        source_type: str,
        content: bytes,
    ) -> tuple[Document, Job]:
        """Persist revision one and its pending indexing job in one transaction.

        Source bytes are written first because object storage cannot participate in
        the PostgreSQL transaction. Any later database failure rolls back document,
        revision, and job together, then best-effort deletes the unreferenced object.
        """
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
            job = await jobs.create_indexing(
                document_id=document.id,
                document_revision=1,
            )
            await self._repository.commit()
            return document, job
        except BaseException:
            await self._rollback()
            try:
                self._object_store.delete(object_key)
            except ObjectStorageError:
                pass
            raise

    async def upsert_for_indexing(
        self,
        *,
        jobs: JobRepository,
        workspace_id: UUID,
        document_id: UUID,
        source_key: str,
        filename: str,
        source_type: str,
        content: bytes,
    ) -> DocumentUpsertResult:
        """Create, return unchanged, or enqueue an immutable replacement.

        The document-scoped transaction lock covers both the SHA-256 comparison
        and mutation creation, so concurrent requests retain one active job.
        """
        content_hash = sha256(content).hexdigest()
        stored_key: str | None = None
        try:
            existing = await self._repository.lock_for_upsert(
                workspace_id=workspace_id, document_id=document_id
            )
            if existing is not None:
                if existing.content_hash == content_hash:
                    await self._repository.commit()
                    return DocumentUpsertResult(
                        action=DocumentUpsertAction.UNCHANGED,
                        document=existing,
                        job=None,
                    )

                def store_replacement(revision_number: int) -> str:
                    nonlocal stored_key
                    stored_key = self.object_key(
                        workspace_id, document_id, revision_number
                    )
                    self._object_store.put(stored_key, content)
                    return self._object_store.uri(stored_key)

                revision = await self._repository.add_revision(
                    workspace_id=workspace_id,
                    document_id=document_id,
                    content_hash=content_hash,
                    object_uri_factory=store_replacement,
                )
                if revision is None:
                    raise LookupError("document disappeared during replacement")
                updating = await self._repository.transition_status(
                    workspace_id=workspace_id,
                    document_id=document_id,
                    from_status=DocumentStatus.READY,
                    to_status=DocumentStatus.UPDATING,
                )
                if updating is None:
                    raise RuntimeError("document is not ready for replacement")
                job = await jobs.create_indexing(
                    document_id=document_id,
                    document_revision=revision.revision,
                )
                await self._repository.commit()
                return DocumentUpsertResult(
                    action=DocumentUpsertAction.UPDATED,
                    document=updating,
                    job=job,
                )

            stored_key = self.object_key(workspace_id, document_id, 1)
            self._object_store.put(stored_key, content)
            document = await self._repository.create(
                document_id=document_id,
                workspace_id=workspace_id,
                source_key=source_key,
                filename=filename,
                source_type=source_type,
                object_uri=self._object_store.uri(stored_key),
                content_hash=content_hash,
            )
            job = await jobs.create_indexing(
                document_id=document.id,
                document_revision=1,
            )
            await self._repository.commit()
            return DocumentUpsertResult(
                action=DocumentUpsertAction.CREATED,
                document=document,
                job=job,
            )
        except BaseException:
            await self._rollback()
            if stored_key is not None:
                try:
                    self._object_store.delete(stored_key)
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
