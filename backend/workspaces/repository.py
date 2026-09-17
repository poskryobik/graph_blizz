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


class WorkspaceRepository:
    """Create and resolve workspaces through an existing async connection."""

    _COLUMNS = "id, name, slug, storage_key, status, created_at, updated_at"

    def __init__(self, connection: AsyncConnection[Any]) -> None:
        """Bind repository operations to a caller-owned database connection."""
        self._connection = connection

    async def create(self, *, name: str, slug: str) -> Workspace:
        """Create a workspace with a database-generated physical storage key."""
        cursor = await self._connection.execute(
            f"""
            INSERT INTO graph_blizz.workspaces (name, slug)
            VALUES (%s, %s)
            RETURNING {self._COLUMNS}
            """,
            (name, slug),
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
        )
