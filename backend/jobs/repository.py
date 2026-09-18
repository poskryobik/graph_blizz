"""PostgreSQL persistence for durable jobs."""

from typing import Any
from uuid import UUID

from psycopg import AsyncConnection

from backend.jobs.models import Job, JobErrorCode, JobStatus, JobType


class JobRepository:
    """Create and resolve durable jobs through a caller-owned transaction."""

    _COLUMNS = (
        "id, document_id, document_revision, job_type, status, attempts, "
        "max_attempts, available_at, created_at, updated_at, lease_owner, "
        "lease_expires_at, heartbeat_at, started_at, finished_at, error_code, "
        "error_detail"
    )

    def __init__(self, connection: AsyncConnection[Any]) -> None:
        """Bind repository operations to a caller-owned database connection."""
        self._connection = connection

    async def create_indexing(
        self, *, document_id: UUID, document_revision: int, max_attempts: int = 3
    ) -> Job:
        """Create a pending indexing job for one immutable document revision."""
        cursor = await self._connection.execute(
            f"""
            INSERT INTO graph_blizz.jobs (
                document_id, document_revision, job_type, max_attempts
            ) VALUES (%s, %s, %s, %s)
            RETURNING {self._COLUMNS}
            """,
            (
                document_id,
                document_revision,
                JobType.INDEX_DOCUMENT.value,
                max_attempts,
            ),
        )
        return self._job(await cursor.fetchone())

    async def get(self, job_id: UUID) -> Job | None:
        """Return a durable job by id, or ``None`` when absent."""
        cursor = await self._connection.execute(
            f"SELECT {self._COLUMNS} FROM graph_blizz.jobs WHERE id = %s",
            (job_id,),
        )
        row = await cursor.fetchone()
        return None if row is None else self._job(row)

    @staticmethod
    def _job(row: tuple[Any, ...] | None) -> Job:
        """Map the fixed repository projection to a validated entity."""
        if row is None:
            raise RuntimeError("job write returned no row")
        return Job(
            id=row[0],
            document_id=row[1],
            document_revision=row[2],
            type=JobType(row[3]),
            status=JobStatus(row[4]),
            attempts=row[5],
            max_attempts=row[6],
            available_at=row[7],
            created_at=row[8],
            updated_at=row[9],
            lease_owner=row[10],
            lease_expires_at=row[11],
            heartbeat_at=row[12],
            started_at=row[13],
            finished_at=row[14],
            error_code=None if row[15] is None else JobErrorCode(row[15]),
            error_detail=row[16],
        )
