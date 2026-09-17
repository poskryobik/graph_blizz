"""Persistence operations for the minimal document registry."""

from typing import Any
from uuid import UUID

from psycopg import AsyncConnection

from backend.documents.models import Document, DocumentStatus


class DocumentRepository:
    """Create and resolve document metadata through an async connection."""

    _COLUMNS = (
        "id, workspace_id, source_key, filename, source_type, object_uri, "
        "content_hash, status, created_at, updated_at"
    )

    def __init__(self, connection: AsyncConnection[Any]) -> None:
        """Bind repository operations to a caller-owned database connection."""
        self._connection = connection

    async def create(
        self,
        *,
        workspace_id: UUID,
        source_key: str,
        filename: str,
        source_type: str,
        object_uri: str,
        content_hash: str,
    ) -> Document:
        """Create an uploaded document with database-generated metadata."""
        cursor = await self._connection.execute(
            f"""
            INSERT INTO graph_blizz.documents (
                workspace_id, source_key, filename, source_type, object_uri,
                content_hash
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING {self._COLUMNS}
            """,
            (
                workspace_id,
                source_key,
                filename,
                source_type,
                object_uri,
                content_hash,
            ),
        )
        return self._document(await cursor.fetchone())

    async def get(
        self,
        workspace_id: UUID,
        document_id: UUID,
    ) -> Document | None:
        """Return a workspace document by id, or ``None`` when absent."""
        cursor = await self._connection.execute(
            f"""
            SELECT {self._COLUMNS}
            FROM graph_blizz.documents
            WHERE workspace_id = %s AND id = %s
            """,
            (workspace_id, document_id),
        )
        row = await cursor.fetchone()
        return None if row is None else self._document(row)

    async def list(self, workspace_id: UUID) -> list[Document]:
        """Return workspace documents in deterministic creation order."""
        cursor = await self._connection.execute(
            f"""
            SELECT {self._COLUMNS}
            FROM graph_blizz.documents
            WHERE workspace_id = %s
            ORDER BY created_at, id
            """,
            (workspace_id,),
        )
        return [self._document(row) for row in await cursor.fetchall()]

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
        )
