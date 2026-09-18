"""Domain model for durable document mutation jobs."""

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID


class JobStatus(StrEnum):
    """Persisted lifecycle states for a durable job."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    RETRY = "RETRY"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class JobType(StrEnum):
    """Job operations understood by the current application."""

    INDEX_DOCUMENT = "INDEX_DOCUMENT"


class JobErrorCode(StrEnum):
    """Allow-listed, non-sensitive failure classifications persisted with a job."""

    INDEXING_FAILED = "INDEXING_FAILED"
    DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"
    ATTEMPTS_EXHAUSTED = "ATTEMPTS_EXHAUSTED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


ACTIVE_JOB_STATUSES = frozenset({JobStatus.PENDING, JobStatus.RUNNING, JobStatus.RETRY})
TERMINAL_JOB_STATUSES = frozenset(
    {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}
)
_TRANSITIONS = {
    JobStatus.PENDING: frozenset({JobStatus.RUNNING, JobStatus.CANCELLED}),
    JobStatus.RUNNING: frozenset(
        {JobStatus.RETRY, JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}
    ),
    JobStatus.RETRY: frozenset({JobStatus.RUNNING, JobStatus.CANCELLED}),
    JobStatus.SUCCEEDED: frozenset(),
    JobStatus.FAILED: frozenset(),
    JobStatus.CANCELLED: frozenset(),
}


def validate_transition(current: JobStatus, target: JobStatus) -> None:
    """Raise when a requested durable job transition is not allowed."""
    if target not in _TRANSITIONS[current]:
        raise ValueError(f"invalid job transition: {current.value} -> {target.value}")


@dataclass(frozen=True, slots=True)
class Job:
    """Validated snapshot of one durable job.

    Persisted error metadata is restricted to an allow-listed classification.
    Tracebacks, messages, request bodies, and credentials must remain in protected
    operational telemetry; ``error_detail`` exists only for schema compatibility and
    is required to be ``None``.
    """

    id: UUID
    document_id: UUID
    document_revision: int
    type: JobType
    status: JobStatus
    attempts: int
    max_attempts: int
    available_at: datetime
    created_at: datetime
    updated_at: datetime
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    heartbeat_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_code: JobErrorCode | None = None
    error_detail: str | None = None

    def __post_init__(self) -> None:
        """Enforce lifecycle, timing, lease, attempt, and error invariants."""
        times = (
            self.available_at,
            self.created_at,
            self.updated_at,
            self.lease_expires_at,
            self.heartbeat_at,
            self.started_at,
            self.finished_at,
        )
        if any(value is not None and value.utcoffset() is None for value in times):
            raise ValueError("job timestamps must be timezone-aware")
        if self.document_revision <= 0:
            raise ValueError("document_revision must be positive")
        if self.max_attempts <= 0 or not 0 <= self.attempts <= self.max_attempts:
            raise ValueError("attempts must be between zero and max_attempts")
        if self.updated_at < self.created_at or self.available_at < self.created_at:
            raise ValueError("job timestamps cannot precede created_at")
        if (self.attempts == 0) != (self.started_at is None):
            raise ValueError("attempts and started_at must be set together")
        if self.status is JobStatus.PENDING and self.attempts != 0:
            raise ValueError("PENDING jobs cannot have attempts")
        if self.status in {
            JobStatus.RUNNING,
            JobStatus.RETRY,
            JobStatus.SUCCEEDED,
            JobStatus.FAILED,
        } and (self.attempts == 0 or self.started_at is None):
            raise ValueError("started jobs require an attempt and started_at")
        if self.started_at is not None and self.started_at < self.created_at:
            raise ValueError("started_at cannot precede created_at")
        if self.started_at is not None and self.updated_at < self.started_at:
            raise ValueError("updated_at cannot precede started_at")
        lease_values = (self.lease_owner, self.lease_expires_at, self.heartbeat_at)
        if self.status is JobStatus.RUNNING:
            if (
                self.lease_owner is None
                or self.lease_expires_at is None
                or self.heartbeat_at is None
                or self.started_at is None
            ):
                raise ValueError(
                    "RUNNING jobs require lease, heartbeat, and started_at"
                )
            if not self.lease_owner.strip() or len(self.lease_owner) > 128:
                raise ValueError("lease_owner must contain 1 to 128 non-blank chars")
            if self.lease_expires_at <= self.heartbeat_at:
                raise ValueError("lease must expire after the heartbeat")
            if self.heartbeat_at < self.started_at:
                raise ValueError("heartbeat cannot precede started_at")
            if self.updated_at < self.heartbeat_at:
                raise ValueError("updated_at cannot precede heartbeat_at")
        elif any(value is not None for value in lease_values):
            raise ValueError("only RUNNING jobs may hold a lease")
        if self.status in TERMINAL_JOB_STATUSES:
            if self.finished_at is None:
                raise ValueError("terminal jobs require finished_at")
        elif self.finished_at is not None:
            raise ValueError("active jobs cannot have finished_at")
        if self.status in {JobStatus.RETRY, JobStatus.FAILED}:
            if self.status is JobStatus.RETRY and self.attempts >= self.max_attempts:
                raise ValueError("RETRY jobs require another available attempt")
        elif self.error_code is not None:
            raise ValueError("error metadata is only valid for RETRY or FAILED jobs")
        if self.error_code is not None and not isinstance(
            self.error_code, JobErrorCode
        ):
            raise ValueError("error_code must be an allow-listed safe classification")
        if self.error_detail is not None:
            raise ValueError("raw error detail must not be persisted")
        if self.finished_at is not None and self.finished_at < (
            self.started_at or self.created_at
        ):
            raise ValueError("finished_at cannot precede job execution")
        if self.finished_at is not None and self.updated_at < self.finished_at:
            raise ValueError("updated_at cannot precede finished_at")

    def transitioned(self, status: JobStatus, **changes: Any) -> "Job":
        """Return a validated snapshot after an allowed lifecycle transition."""
        validate_transition(self.status, status)
        return replace(self, status=status, **changes)
