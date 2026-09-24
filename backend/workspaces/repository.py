"""Persistence operations for workspace metadata."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection


class WorkspaceStatus(StrEnum):
    """Workspace lifecycle states stored in PostgreSQL."""

    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


@dataclass(frozen=True, slots=True)
class Workspace:
    """Persistent workspace metadata resolved by the server."""

    id: UUID
    name: str
    slug: str
    storage_key: str
    status: WorkspaceStatus
    created_at: datetime
    updated_at: datetime
    description: str | None = None
    index_schema_version: int = 1
    embedding_profile: str = (
        '{"dimension":null,"model":"ai-sage/Giga-Embeddings-instruct-480M-0826",'
        '"normalization":true}'
    )


class WorkspaceRepository:
    """Create and resolve workspaces through an existing async connection."""

    _COLUMNS = (
        "id, name, slug, storage_key, status, created_at, updated_at, "
        "index_schema_version, embedding_profile, description"
    )

    def __init__(self, connection: AsyncConnection[Any]) -> None:
        """Bind repository operations to a caller-owned database connection."""
        self._connection = connection

    async def create(
        self,
        *,
        name: str,
        slug: str,
        description: str | None = None,
    ) -> Workspace:
        """Create a workspace with a database-generated physical storage key."""
        cursor = await self._connection.execute(
            f"""
            INSERT INTO graph_blizz.workspaces (name, slug, description)
            VALUES (%s, %s, %s)
            RETURNING {self._COLUMNS}
            """,
            (name, slug, self._normalize_description(description)),
        )
        return self._workspace(await cursor.fetchone())

    async def get(self, workspace_id: UUID) -> Workspace | None:
        """Return a workspace by id, or ``None`` when it does not exist."""
        cursor = await self._connection.execute(
            f"""
            SELECT {self._COLUMNS}
            FROM graph_blizz.workspaces
            WHERE id = %s
            """,
            (workspace_id,),
        )
        row = await cursor.fetchone()
        return None if row is None else self._workspace(row)

    async def rename(
        self,
        workspace_id: UUID,
        *,
        name: str,
        slug: str,
    ) -> Workspace | None:
        """Rename a workspace without changing its physical storage key."""
        cursor = await self._connection.execute(
            f"""
            UPDATE graph_blizz.workspaces
            SET name = %s, slug = %s, updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            RETURNING {self._COLUMNS}
            """,
            (name, slug, workspace_id),
        )
        row = await cursor.fetchone()
        return None if row is None else self._workspace(row)

    async def ensure_index_contract(
        self,
        workspace_id: UUID,
        *,
        index_schema_version: int,
        embedding_profile: str,
    ) -> Workspace | None:
        """Persist a contract change and mark prior revisions for reindex.

        Both changes occur in the caller's transaction. An unchanged contract is
        a no-op, while an incompatible contract marks every non-deleted source
        revision before the workspace starts writing to its new namespace.
        """
        locked = await self._connection.execute(
            f"""
            SELECT {self._COLUMNS}
            FROM graph_blizz.workspaces
            WHERE id = %s
            FOR UPDATE
            """,
            (workspace_id,),
        )
        row = await locked.fetchone()
        if row is None:
            return None
        workspace = self._workspace(row)
        if (
            workspace.index_schema_version == index_schema_version
            and workspace.embedding_profile == embedding_profile
        ):
            return workspace

        cursor = await self._connection.execute(
            f"""
            WITH changed AS (
                UPDATE graph_blizz.workspaces
                SET index_schema_version = %s, embedding_profile = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                RETURNING {self._COLUMNS}
            ), marked AS (
                UPDATE graph_blizz.document_revisions AS r
                SET requires_reindex = TRUE
                FROM graph_blizz.documents AS d, changed
                WHERE r.document_id = d.id AND d.workspace_id = changed.id
                  AND r.revision = d.active_revision
                  AND d.status <> 'DELETED'
                RETURNING r.document_id
            ), marking_complete AS (
                SELECT count(*) FROM marked
            )
            SELECT changed.*
            FROM changed CROSS JOIN marking_complete
            """,
            (
                index_schema_version,
                embedding_profile,
                workspace_id,
            ),
        )
        row = await cursor.fetchone()
        return None if row is None else self._workspace(row)

    @staticmethod
    def _normalize_description(description: str | None) -> str | None:
        """Trim a description and treat a blank value as absent."""
        if description is None:
            return None
        normalized = description.strip()
        return normalized or None

    @staticmethod
    def _workspace(row: tuple[Any, ...] | None) -> Workspace:
        """Map the fixed repository projection to a workspace entity."""
        if row is None:
            raise RuntimeError("workspace write returned no row")
        return Workspace(
            id=row[0],
            name=row[1],
            slug=row[2],
            storage_key=row[3],
            status=WorkspaceStatus(row[4]),
            created_at=row[5],
            updated_at=row[6],
            index_schema_version=row[7] if len(row) > 7 else 1,
            embedding_profile=(
                row[8]
                if len(row) > 8
                else '{"dimension":null,"model":"ai-sage/'
                'Giga-Embeddings-instruct-480M-0826","normalization":true}'
            ),
            description=row[9] if len(row) > 9 else None,
        )
