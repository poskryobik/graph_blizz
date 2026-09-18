"""Production persistence and indexing adapters for the leased worker."""

from datetime import timedelta
from typing import Any
from uuid import UUID

import psycopg
from lightrag.utils import compute_mdhash_id  # type: ignore[import-untyped]
from psycopg import AsyncConnection

from backend.config import ApplicationSettings, PostgreSQLSettings
from backend.documents import (
    Document,
    DocumentRepository,
    DocumentSourceService,
    DocumentStatus,
)
from backend.indexing import IndexingService
from backend.jobs import Job, JobErrorCode, JobRepository
from backend.parsers import create_default_parser_registry
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
        self, *, job_id: UUID, owner: str, lease_for: timedelta
    ) -> Job | None:
        async with await connect_postgres(self._settings) as connection:
            repository = JobRepository(connection)
            job = await repository.heartbeat(
                job_id=job_id, owner=owner, lease_for=lease_for
            )
            await repository.commit()
            return job

    async def succeed(self, *, job_id: UUID, owner: str) -> Job | None:
        async with await connect_postgres(self._settings) as connection:
            repository = JobRepository(connection)
            job = await repository.succeed(job_id=job_id, owner=owner)
            await repository.commit()
            return job

    async def fail(
        self,
        *,
        job_id: UUID,
        owner: str,
        error_code: JobErrorCode,
        retry_after: timedelta,
    ) -> Job | None:
        async with await connect_postgres(self._settings) as connection:
            repository = JobRepository(connection)
            job = await repository.fail(
                job_id=job_id,
                owner=owner,
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
            if document.status is DocumentStatus.READY:
                return
            active = await documents.get(workspace_id, job.document_id)
            if active is None:
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

            workspace = await WorkspaceRepository(connection).get(workspace_id)
            if workspace is None or workspace.status is not WorkspaceStatus.ACTIVE:
                raise LookupError("job workspace is unavailable")
            context = AuthorizedWorkspaceContext(
                principal_id=self._settings.auth.demo_owner_id,
                principal_type=PrincipalType.USER,
                workspace_id=workspace.id,
                storage_key=workspace.storage_key,
                permissions=ALL_OWNER_PERMISSIONS,
            )
            service = IndexingService(
                documents,
                DocumentSourceService(documents, self._object_store),
                self._parsers,
                self._runtimes,
            )
            if (
                document.status is DocumentStatus.UPDATING
                and job.document_revision != active.active_revision
            ):
                await self._replace(
                    documents=documents,
                    sources=DocumentSourceService(documents, self._object_store),
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
        await runtime.rag.adelete_by_doc_id(
            compute_mdhash_id(old_content, prefix="doc-")
        )
        await runtime.rag.ainsert(new_content)
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

    def _parse(self, sources: DocumentSourceService, document: Document) -> str:
        source = sources.read(document).decode("utf-8")
        parser = self._parsers.get_parser(
            media_type=document.source_type,
            filename=document.filename,
        )
        return parser.parse(source, source_name=document.filename).content

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
