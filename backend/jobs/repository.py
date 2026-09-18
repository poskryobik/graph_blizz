"""PostgreSQL persistence for durable jobs."""

from datetime import timedelta
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
    _JOB_COLUMNS = (
        "job.id, job.document_id, job.document_revision, job.job_type, job.status, "
        "job.attempts, job.max_attempts, job.available_at, job.created_at, "
        "job.updated_at, job.lease_owner, job.lease_expires_at, job.heartbeat_at, "
        "job.started_at, job.finished_at, job.error_code, job.error_detail"
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

    async def claim_next(self, *, owner: str, lease_for: timedelta) -> Job | None:
        """Atomically claim one available job without blocking other workers."""
        self._validate_lease(owner, lease_for)
        cursor = await self._connection.execute(
            f"""
            WITH candidate AS (
                SELECT id
                FROM graph_blizz.jobs
                WHERE status IN ('PENDING', 'RETRY')
                  AND available_at <= now()
                  AND attempts < max_attempts
                ORDER BY available_at, created_at, id
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            UPDATE graph_blizz.jobs AS job
            SET status = 'RUNNING', attempts = job.attempts + 1,
                lease_owner = %s, heartbeat_at = now(),
                lease_expires_at = now() + %s,
                started_at = COALESCE(job.started_at, now()),
                updated_at = now(), error_code = NULL, error_detail = NULL
            FROM candidate
            WHERE job.id = candidate.id
            RETURNING {self._JOB_COLUMNS}
            """,
            (owner, lease_for),
        )
        row = await cursor.fetchone()
        return None if row is None else self._job(row)

    async def heartbeat(
        self, *, job_id: UUID, owner: str, lease_for: timedelta
    ) -> Job | None:
        """Extend an unexpired lease only when ``owner`` still owns the job."""
        self._validate_lease(owner, lease_for)
        cursor = await self._connection.execute(
            f"""
            UPDATE graph_blizz.jobs
            SET heartbeat_at = now(), lease_expires_at = now() + %s,
                updated_at = now()
            WHERE id = %s AND status = 'RUNNING' AND lease_owner = %s
              AND lease_expires_at > now()
            RETURNING {self._COLUMNS}
            """,
            (lease_for, job_id, owner),
        )
        row = await cursor.fetchone()
        return None if row is None else self._job(row)

    async def succeed(self, *, job_id: UUID, owner: str) -> Job | None:
        """Complete a running job when its owner still has a live lease."""
        cursor = await self._connection.execute(
            f"""
            UPDATE graph_blizz.jobs
            SET status = 'SUCCEEDED', lease_owner = NULL,
                lease_expires_at = NULL, heartbeat_at = NULL,
                finished_at = now(), updated_at = now()
            WHERE id = %s AND status = 'RUNNING' AND lease_owner = %s
              AND lease_expires_at > now()
            RETURNING {self._COLUMNS}
            """,
            (job_id, owner),
        )
        row = await cursor.fetchone()
        return None if row is None else self._job(row)

    async def fail(
        self,
        *,
        job_id: UUID,
        owner: str,
        error_code: JobErrorCode,
        retry_after: timedelta,
    ) -> Job | None:
        """Schedule a safe retry or terminal failure for the current owner."""
        if retry_after.total_seconds() < 0:
            raise ValueError("retry_after must not be negative")
        cursor = await self._connection.execute(
            f"""
            UPDATE graph_blizz.jobs
            SET status = CASE WHEN attempts < max_attempts THEN 'RETRY' ELSE 'FAILED' END,
                available_at = CASE WHEN attempts < max_attempts
                    THEN now() + %s ELSE available_at END,
                lease_owner = NULL, lease_expires_at = NULL, heartbeat_at = NULL,
                finished_at = CASE WHEN attempts < max_attempts THEN NULL ELSE now() END,
                error_code = CASE WHEN attempts < max_attempts THEN %s
                    ELSE 'ATTEMPTS_EXHAUSTED' END,
                error_detail = NULL, updated_at = now()
            WHERE id = %s AND status = 'RUNNING' AND lease_owner = %s
              AND lease_expires_at > now()
            RETURNING {self._COLUMNS}
            """,
            (retry_after, error_code.value, job_id, owner),
        )
        row = await cursor.fetchone()
        return None if row is None else self._job(row)

    async def recover_expired(self, *, retry_after: timedelta) -> list[Job]:
        """Recover expired leases, preserving attempts and terminal timestamps."""
        if retry_after.total_seconds() < 0:
            raise ValueError("retry_after must not be negative")
        cursor = await self._connection.execute(
            f"""
            UPDATE graph_blizz.jobs
            SET status = CASE WHEN attempts < max_attempts THEN 'RETRY' ELSE 'FAILED' END,
                available_at = CASE WHEN attempts < max_attempts
                    THEN now() + %s ELSE available_at END,
                lease_owner = NULL, lease_expires_at = NULL, heartbeat_at = NULL,
                finished_at = CASE WHEN attempts < max_attempts THEN NULL ELSE now() END,
                error_code = CASE WHEN attempts < max_attempts
                    THEN 'DEPENDENCY_UNAVAILABLE' ELSE 'ATTEMPTS_EXHAUSTED' END,
                error_detail = NULL, updated_at = now()
            WHERE status = 'RUNNING' AND lease_expires_at <= now()
            RETURNING {self._COLUMNS}
            """,
            (retry_after,),
        )
        return [self._job(row) for row in await cursor.fetchall()]

    async def commit(self) -> None:
        """Commit the caller-owned transaction."""
        await self._connection.commit()

    async def rollback(self) -> None:
        """Roll back the caller-owned transaction."""
        await self._connection.rollback()

    @staticmethod
    def _validate_lease(owner: str, lease_for: timedelta) -> None:
        if not owner.strip() or len(owner) > 128:
            raise ValueError("owner must contain 1 to 128 non-blank chars")
        if lease_for.total_seconds() <= 0:
            raise ValueError("lease_for must be positive")

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
