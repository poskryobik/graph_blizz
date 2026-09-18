"""Persistence operations for the minimal document registry."""

from collections.abc import Callable
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection

from backend.documents.models import Document, DocumentRevision, DocumentStatus


class DocumentScopeConflictError(RuntimeError):
    """Signal that a document id belongs to a different workspace."""


class DocumentRepository:
    """Create and resolve document metadata through an async connection."""

    _COLUMNS = (
        "d.id, d.workspace_id, d.source_key, d.filename, d.source_type, "
        "r.object_uri, r.content_hash, d.status, d.created_at, d.updated_at, "
        "d.active_revision"
    )

    def __init__(self, connection: AsyncConnection[Any]) -> None:
        """Bind repository operations to a caller-owned database connection."""
        self._connection = connection

    @property
    def connection(self) -> AsyncConnection[Any]:
        """Expose the caller-owned connection for one shared transaction."""
        return self._connection

    async def commit(self) -> None:
        """Commit the current document transaction."""
        await self._connection.commit()

    async def rollback(self) -> None:
        """Roll back the current document transaction."""
        await self._connection.rollback()

    async def lock_for_upsert(
        self, *, workspace_id: UUID, document_id: UUID
    ) -> Document | None:
        """Serialize one logical document upsert and return its active revision.

        A transaction-scoped advisory lock is used because a row lock cannot
        serialize concurrent writers before the document row exists.
        """
        await self._connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (str(document_id),),
        )
        document = await self.get(workspace_id, document_id)
        if document is not None:
            return document
        cursor = await self._connection.execute(
            "SELECT 1 FROM graph_blizz.documents WHERE id = %s",
            (document_id,),
        )
        if await cursor.fetchone() is not None:
            raise DocumentScopeConflictError("document belongs to another workspace")
        return None

    async def create(
        self,
        *,
        document_id: UUID | None = None,
        workspace_id: UUID,
        source_key: str,
        filename: str,
        source_type: str,
        object_uri: str,
        content_hash: str,
    ) -> Document:
        """Create a logical document and immutable revision 1 atomically."""
        cursor = await self._connection.execute(
            f"""
            WITH inserted_document AS (
                INSERT INTO graph_blizz.documents (
                    id, workspace_id, source_key, filename, source_type
                )
                VALUES (COALESCE(%s, gen_random_uuid()), %s, %s, %s, %s)
                RETURNING *
            ), inserted_revision AS (
                INSERT INTO graph_blizz.document_revisions (
                    document_id, revision, object_uri, content_hash
                )
                SELECT id, 1, %s, %s FROM inserted_document
                RETURNING *
            )
            SELECT {self._COLUMNS}
            FROM inserted_document AS d
            JOIN inserted_revision AS r ON r.document_id = d.id
            """,
            (
                document_id,
                workspace_id,
                source_key,
                filename,
                source_type,
                object_uri,
                content_hash,
            ),
        )
        return self._document(await cursor.fetchone())

    async def add_revision(
        self,
        *,
        workspace_id: UUID,
        document_id: UUID,
        content_hash: str,
        object_uri_factory: Callable[[int], str],
    ) -> DocumentRevision | None:
        """Append the next revision while serializing writers per document.

        The factory runs only after the document row is locked and receives the
        database-derived next number, allowing its immutable object key to include
        that number. The caller owns the transaction and activates separately.
        """
        lock = await self._connection.execute(
            """
            SELECT id
            FROM graph_blizz.documents
            WHERE workspace_id = %s AND id = %s
            FOR UPDATE
            """,
            (workspace_id, document_id),
        )
        if await lock.fetchone() is None:
            return None
        cursor = await self._connection.execute(
            """
            SELECT COALESCE(MAX(revision), 0) + 1
            FROM graph_blizz.document_revisions
            WHERE document_id = %s
            """,
            (document_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("revision sequence query returned no row")
        revision = int(row[0])
        object_uri = object_uri_factory(revision)
        inserted = await self._connection.execute(
            """
            INSERT INTO graph_blizz.document_revisions (
                document_id, revision, object_uri, content_hash
            )
            VALUES (%s, %s, %s, %s)
            RETURNING document_id, revision, object_uri, content_hash, created_at
            """,
            (document_id, revision, object_uri, content_hash),
        )
        return self._revision(await inserted.fetchone())

    async def activate_revision(
        self, *, workspace_id: UUID, document_id: UUID, revision: int
    ) -> Document | None:
        """Select an existing revision as the document's active projection."""
        cursor = await self._connection.execute(
            """
            UPDATE graph_blizz.documents AS d
            SET active_revision = %s, updated_at = now()
            FROM graph_blizz.document_revisions AS candidate
            WHERE d.workspace_id = %s AND d.id = %s
              AND candidate.document_id = d.id AND candidate.revision = %s
            RETURNING d.id
            """,
            (revision, workspace_id, document_id, revision),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return await self.get(workspace_id, document_id)

    async def get(
        self,
        workspace_id: UUID,
        document_id: UUID,
    ) -> Document | None:
        """Return a workspace document by id, or ``None`` when absent."""
        cursor = await self._connection.execute(
            f"""
            SELECT {self._COLUMNS}
            FROM graph_blizz.documents AS d
            JOIN graph_blizz.document_revisions AS r
              ON r.document_id = d.id AND r.revision = d.active_revision
            WHERE d.workspace_id = %s AND d.id = %s
            """,
            (workspace_id, document_id),
        )
        row = await cursor.fetchone()
        return None if row is None else self._document(row)

    async def get_revision(
        self, workspace_id: UUID, document_id: UUID, revision: int
    ) -> Document | None:
        """Return metadata projected onto one exact immutable revision."""
        cursor = await self._connection.execute(
            f"""
            SELECT {self._COLUMNS}
            FROM graph_blizz.documents AS d
            JOIN graph_blizz.document_revisions AS r
              ON r.document_id = d.id AND r.revision = %s
            WHERE d.workspace_id = %s AND d.id = %s
              AND d.active_revision = %s
            """,
            (revision, workspace_id, document_id, revision),
        )
        row = await cursor.fetchone()
        return None if row is None else self._document(row)

    async def list(self, workspace_id: UUID) -> list[Document]:
        """Return workspace documents in deterministic creation order."""
        cursor = await self._connection.execute(
            f"""
            SELECT {self._COLUMNS}
            FROM graph_blizz.documents AS d
            JOIN graph_blizz.document_revisions AS r
              ON r.document_id = d.id AND r.revision = d.active_revision
            WHERE d.workspace_id = %s
            ORDER BY d.created_at, d.id
            """,
            (workspace_id,),
        )
        return [self._document(row) for row in await cursor.fetchall()]

    async def transition_status(
        self,
        *,
        workspace_id: UUID,
        document_id: UUID,
        from_status: DocumentStatus,
        to_status: DocumentStatus,
    ) -> Document | None:
        """Atomically change status when the document is in the expected state."""
        cursor = await self._connection.execute(
            f"""
            UPDATE graph_blizz.documents AS d
            SET status = %s, updated_at = now()
            FROM graph_blizz.document_revisions AS r
            WHERE d.workspace_id = %s AND d.id = %s AND d.status = %s
              AND r.document_id = d.id AND r.revision = d.active_revision
            RETURNING {self._COLUMNS}
            """,
            (to_status.value, workspace_id, document_id, from_status.value),
        )
        row = await cursor.fetchone()
        return None if row is None else self._document(row)

    async def transition_status_for_job(
        self,
        *,
        workspace_id: UUID,
        document_id: UUID,
        from_status: DocumentStatus,
        to_status: DocumentStatus,
        job_id: UUID,
        lease_owner: str,
        attempt: int,
    ) -> Document | None:
        """Change status only while the exact processing attempt owns its lease."""
        cursor = await self._connection.execute(
            f"""
            UPDATE graph_blizz.documents AS d
            SET status = %s, updated_at = now()
            FROM graph_blizz.document_revisions AS r
            WHERE d.workspace_id = %s AND d.id = %s AND d.status = %s
              AND r.document_id = d.id AND r.revision = d.active_revision
              AND EXISTS (
                  SELECT 1 FROM graph_blizz.jobs AS j
                  WHERE j.id = %s AND j.document_id = d.id
                    AND j.document_revision = d.active_revision
                    AND j.status = 'RUNNING' AND j.lease_owner = %s
                    AND j.attempts = %s AND j.lease_expires_at > now()
              )
            RETURNING {self._COLUMNS}
            """,
            (
                to_status.value,
                workspace_id,
                document_id,
                from_status.value,
                job_id,
                lease_owner,
                attempt,
            ),
        )
        row = await cursor.fetchone()
        return None if row is None else self._document(row)

    @staticmethod
    def _document(row: tuple[Any, ...] | None) -> Document:
        """Map the fixed repository projection to a document entity."""
        if row is None:
            raise RuntimeError("document write returned no row")
        return Document(
            id=row[0],
            workspace_id=row[1],
            source_key=row[2],
            filename=row[3],
            source_type=row[4],
            object_uri=row[5],
            content_hash=row[6],
            status=DocumentStatus(row[7]),
            created_at=row[8],
            updated_at=row[9],
            active_revision=row[10],
        )

    @staticmethod
    def _revision(row: tuple[Any, ...] | None) -> DocumentRevision:
        """Map the fixed revision projection to an immutable entity."""
        if row is None:
            raise RuntimeError("document revision write returned no row")
        return DocumentRevision(
            document_id=row[0],
            revision=row[1],
            object_uri=row[2],
            content_hash=row[3],
            created_at=row[4],
        )
