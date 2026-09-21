"""Production persistence and indexing adapters for the leased worker."""

from datetime import timedelta
from typing import Any
from uuid import UUID

import psycopg
from psycopg import AsyncConnection

from backend.config import ApplicationSettings, PostgreSQLSettings
from backend.documents import (
    Document,
    DocumentRepository,
    DocumentSourceService,
    DocumentStatus,
)
from backend.index_versions import active_index_contract
from backend.indexing import IndexingService
from backend.indexing.provenance import delete_indexed_document, insert_parsed_document
from backend.jobs import Job, JobErrorCode, JobRepository, JobType
from backend.parsers import ParsedDocument, create_default_parser_registry
from backend.rag import LightRAGRuntimeRegistry
from backend.security import (
    ALL_OWNER_PERMISSIONS,
    AuthorizedWorkspaceContext,
    PrincipalType,
)
from backend.storage import ObjectStore
from backend.workspaces import WorkspaceRepository, WorkspaceStatus


async def connect_postgres(
    settings: PostgreSQLSettings,
) -> AsyncConnection[Any]:
    """Open one async PostgreSQL connection from validated settings."""
    password = (
        settings.password.get_secret_value() if settings.password is not None else None
    )
    return await psycopg.AsyncConnection.connect(
        host=settings.host,
        port=settings.port,
        dbname=settings.database,
        user=settings.username,
        password=password,
        sslmode=settings.ssl_mode,
    )


class PostgreSQLJobStore:
    """Run every short job state transition in its own committed transaction."""

    def __init__(self, settings: PostgreSQLSettings) -> None:
        self._settings = settings

    async def recover_expired(self, *, retry_after: timedelta) -> list[Job]:
        async with await connect_postgres(self._settings) as connection:
            repository = JobRepository(connection)
            jobs = await repository.recover_expired(retry_after=retry_after)
            await repository.commit()
            return jobs

    async def claim_next(self, *, owner: str, lease_for: timedelta) -> Job | None:
        async with await connect_postgres(self._settings) as connection:
            repository = JobRepository(connection)
            job = await repository.claim_next(owner=owner, lease_for=lease_for)
            await repository.commit()
            return job

    async def heartbeat(
        self, *, job_id: UUID, owner: str, attempt: int, lease_for: timedelta
    ) -> Job | None:
        async with await connect_postgres(self._settings) as connection:
            repository = JobRepository(connection)
            job = await repository.heartbeat(
                job_id=job_id, owner=owner, attempt=attempt, lease_for=lease_for
            )
            await repository.commit()
            return job

    async def succeed(self, *, job_id: UUID, owner: str, attempt: int) -> Job | None:
        async with await connect_postgres(self._settings) as connection:
            repository = JobRepository(connection)
            job = await repository.succeed(job_id=job_id, owner=owner, attempt=attempt)
            await repository.commit()
            return job

    async def fail(
        self,
        *,
        job_id: UUID,
        owner: str,
        attempt: int,
        error_code: JobErrorCode,
        retry_after: timedelta,
    ) -> Job | None:
        async with await connect_postgres(self._settings) as connection:
            repository = JobRepository(connection)
            job = await repository.fail(
                job_id=job_id,
                owner=owner,
                attempt=attempt,
                error_code=error_code,
                retry_after=retry_after,
            )
            await repository.commit()
            return job


class LeasedDocumentRepository(DocumentRepository):
    """Fence worker document transitions with one live job attempt."""

    def __init__(self, connection: AsyncConnection[Any], job: Job) -> None:
        super().__init__(connection)
        if job.lease_owner is None:
            raise ValueError("processing job must have a lease owner")
        self._job = job
        self._lease_owner = job.lease_owner

    async def transition_status(
        self,
        *,
        workspace_id: UUID,
        document_id: UUID,
        from_status: DocumentStatus,
        to_status: DocumentStatus,
    ) -> Document | None:
        """Apply a transition only while this claimed attempt remains current."""
        return await self.transition_status_for_job(
            workspace_id=workspace_id,
            document_id=document_id,
            from_status=from_status,
            to_status=to_status,
            job_id=self._job.id,
            lease_owner=self._lease_owner,
            attempt=self._job.attempts,
        )


class IndexingJobProcessor:
    """Resolve a job revision and execute it through ``IndexingService``."""

    def __init__(
        self, settings: ApplicationSettings, runtimes: LightRAGRuntimeRegistry
    ) -> None:
        self._settings = settings
        self._runtimes = runtimes
        self._object_store = ObjectStore(settings.minio)
        self._parsers = create_default_parser_registry()

    async def process(self, job: Job) -> None:
        """Index the exact revision referenced by ``job`` under its document lock."""
        async with await connect_postgres(self._settings.postgres) as connection:
            await connection.execute(
                "SELECT pg_advisory_lock(%s)",
                (self._document_lock_key(job.document_id),),
            )
            documents = LeasedDocumentRepository(connection, job)
            workspace_id = await self._workspace_id(connection, job.document_id)
            document = await documents.get_revision(
                workspace_id, job.document_id, job.document_revision
            )
            if document is None:
                raise LookupError("job document revision is unavailable")
            if (
                document.status is DocumentStatus.READY
                and job.type is JobType.INDEX_DOCUMENT
            ):
                return
            active = await documents.get(workspace_id, job.document_id)
            if active is None and document.active_revision is not None:
                raise LookupError("job document is unavailable")
            if document.status in {DocumentStatus.FAILED, DocumentStatus.INDEXING}:
                reset = await documents.transition_status(
                    workspace_id=workspace_id,
                    document_id=document.id,
                    from_status=document.status,
                    to_status=DocumentStatus.UPLOADED,
                )
                if reset is None:
                    raise RuntimeError("document retry state changed concurrently")
                await documents.commit()
                document = reset

            workspace_repository = WorkspaceRepository(connection)
            contract = active_index_contract(self._settings.embedding)
            workspace = await workspace_repository.ensure_index_contract(
                workspace_id,
                index_schema_version=contract.index_schema_version,
                embedding_profile=contract.embedding_profile,
            )
            if workspace is None or workspace.status is not WorkspaceStatus.ACTIVE:
                raise LookupError("job workspace is unavailable")
            context = AuthorizedWorkspaceContext(
                principal_id=self._settings.auth.demo_owner_id,
                principal_type=PrincipalType.USER,
                workspace_id=workspace.id,
                storage_key=workspace.storage_key,
                permissions=ALL_OWNER_PERMISSIONS,
                index_schema_version=workspace.index_schema_version,
                embedding_profile=workspace.embedding_profile,
            )
            sources = DocumentSourceService(documents, self._object_store)
            if job.type is JobType.DELETE_DOCUMENT:
                await self._delete(
                    documents=documents,
                    sources=sources,
                    context=context,
                    document=document,
                    job=job,
                )
                return
            service = IndexingService(
                documents,
                sources,
                self._parsers,
                self._runtimes,
            )
            if (
                active is not None
                and document.status is DocumentStatus.UPDATING
                and job.document_revision != active.active_revision
            ):
                await self._replace(
                    documents=documents,
                    sources=sources,
                    context=context,
                    active=active,
                    replacement=document,
                    job=job,
                )
                return
            await service.index(context, document)

    async def _replace(
        self,
        *,
        documents: LeasedDocumentRepository,
        sources: DocumentSourceService,
        context: AuthorizedWorkspaceContext,
        active: Document,
        replacement: Document,
        job: Job,
    ) -> None:
        """Delete the active LightRAG document, index replacement, then activate it."""
        old_content = self._parse(sources, active)
        new_content = self._parse(sources, replacement)
        runtime = await self._runtimes.get(context)
        await delete_indexed_document(runtime.rag, active, old_content)
        await insert_parsed_document(
            runtime.rag,
            replacement,
            new_content,
            revision=job.document_revision,
        )
        completed = await documents.complete_replacement_for_job(
            workspace_id=context.workspace_id,
            document_id=replacement.id,
            revision=job.document_revision,
            job_id=job.id,
            lease_owner=job.lease_owner or "",
            attempt=job.attempts,
        )
        if completed is None:
            raise RuntimeError("replacement job lease ownership was lost")
        await documents.commit()

    async def _delete(
        self,
        *,
        documents: LeasedDocumentRepository,
        sources: DocumentSourceService,
        context: AuthorizedWorkspaceContext,
        document: Document,
        job: Job,
    ) -> None:
        """Delete the exact active LightRAG document, then complete atomically."""
        if document.status is not DocumentStatus.DELETING:
            raise RuntimeError("document is not deleting")
        if document.active_revision != job.document_revision:
            raise RuntimeError("deletion job revision is not active")
        content = self._parse(sources, document)
        runtime = await self._runtimes.get(context)
        await delete_indexed_document(runtime.rag, document, content)
        completed = await documents.complete_deletion_for_job(
            workspace_id=context.workspace_id,
            document_id=document.id,
            revision=job.document_revision,
            job_id=job.id,
            lease_owner=job.lease_owner or "",
            attempt=job.attempts,
        )
        if completed is None:
            raise RuntimeError("deletion job lease ownership was lost")
        await documents.commit()

    def _parse(
        self, sources: DocumentSourceService, document: Document
    ) -> ParsedDocument:
        source = sources.read(document).decode("utf-8")
        parser = self._parsers.get_parser(
            media_type=document.source_type,
            filename=document.filename,
        )
        return parser.parse(source, source_name=document.filename)

    @staticmethod
    def _document_lock_key(document_id: UUID) -> int:
        """Map a document UUID to PostgreSQL's signed advisory-lock key space."""
        key = document_id.int & ((1 << 64) - 1)
        return key if key < (1 << 63) else key - (1 << 64)

    @staticmethod
    async def _workspace_id(
        connection: AsyncConnection[Any], document_id: UUID
    ) -> UUID:
        cursor = await connection.execute(
            "SELECT workspace_id FROM graph_blizz.documents WHERE id = %s",
            (document_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise LookupError("job document is unavailable")
        return row[0]
