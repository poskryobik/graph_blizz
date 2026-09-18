"""Unit coverage for the durable job state machine."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from backend.jobs import Job, JobErrorCode, JobStatus, JobType, validate_transition

pytestmark = pytest.mark.unit
NOW = datetime(2026, 9, 18, 12, tzinfo=UTC)


def test_allowed_transition_produces_valid_running_job() -> None:
    job = _job()

    running = job.transitioned(
        JobStatus.RUNNING,
        attempts=1,
        lease_owner="worker-1",
        started_at=NOW,
        heartbeat_at=NOW,
        lease_expires_at=NOW + timedelta(minutes=1),
    )

    assert running.status is JobStatus.RUNNING
    assert running.attempts == 1


@pytest.mark.parametrize("current", list(JobStatus))
@pytest.mark.parametrize("target", list(JobStatus))
def test_complete_transition_matrix(current: JobStatus, target: JobStatus) -> None:
    allowed = {
        (JobStatus.PENDING, JobStatus.RUNNING),
        (JobStatus.PENDING, JobStatus.CANCELLED),
        (JobStatus.RUNNING, JobStatus.RETRY),
        (JobStatus.RUNNING, JobStatus.SUCCEEDED),
        (JobStatus.RUNNING, JobStatus.FAILED),
        (JobStatus.RUNNING, JobStatus.CANCELLED),
        (JobStatus.RETRY, JobStatus.RUNNING),
        (JobStatus.RETRY, JobStatus.CANCELLED),
    }

    if (current, target) in allowed:
        validate_transition(current, target)
    else:
        with pytest.raises(ValueError, match="invalid job transition"):
            validate_transition(current, target)


def test_running_job_requires_one_complete_valid_lease() -> None:
    with pytest.raises(ValueError, match="require lease"):
        _job(status=JobStatus.RUNNING, attempts=1, started_at=NOW)
    with pytest.raises(ValueError, match="expire after"):
        _job(
            status=JobStatus.RUNNING,
            attempts=1,
            started_at=NOW,
            heartbeat_at=NOW,
            lease_expires_at=NOW,
            lease_owner="worker",
        )
    for invalid_owner in (" \t ", "w" * 129):
        with pytest.raises(ValueError, match="1 to 128"):
            _job(
                status=JobStatus.RUNNING,
                attempts=1,
                started_at=NOW,
                heartbeat_at=NOW,
                lease_expires_at=NOW + timedelta(minutes=1),
                lease_owner=invalid_owner,
            )


def test_attempt_and_terminal_timestamp_invariants_are_explicit() -> None:
    with pytest.raises(ValueError, match="between zero"):
        _job(attempts=4)
    with pytest.raises(ValueError, match="finished_at"):
        _job(status=JobStatus.SUCCEEDED, attempts=1, started_at=NOW)
    completed = _job(
        status=JobStatus.SUCCEEDED, attempts=1, started_at=NOW, finished_at=NOW
    )
    assert completed.finished_at == NOW


def test_attempts_and_timestamps_cannot_form_contradictory_snapshots() -> None:
    later = NOW + timedelta(seconds=1)
    with pytest.raises(ValueError, match="attempts and started_at"):
        _job(
            status=JobStatus.CANCELLED,
            attempts=1,
            finished_at=NOW,
        )
    with pytest.raises(ValueError, match="attempts and started_at"):
        _job(started_at=NOW)
    with pytest.raises(ValueError, match="updated_at cannot precede heartbeat_at"):
        _job(
            status=JobStatus.RUNNING,
            attempts=1,
            started_at=NOW,
            heartbeat_at=later,
            lease_expires_at=later + timedelta(minutes=1),
            lease_owner="worker",
        )
    with pytest.raises(ValueError, match="updated_at cannot precede finished_at"):
        _job(
            status=JobStatus.SUCCEEDED,
            attempts=1,
            started_at=NOW,
            finished_at=later,
        )


def test_error_metadata_is_bounded_and_only_keeps_safe_summaries() -> None:
    failed = _job(
        status=JobStatus.FAILED,
        attempts=3,
        started_at=NOW,
        finished_at=NOW,
        error_code=JobErrorCode.DEPENDENCY_UNAVAILABLE,
    )
    assert failed.error_code is JobErrorCode.DEPENDENCY_UNAVAILABLE
    for unsafe_detail in (
        "Traceback: ValueError secret-token",
        "api_key=supersecret",
    ):
        with pytest.raises(ValueError, match="must not be persisted"):
            _job(
                status=JobStatus.RETRY,
                attempts=1,
                started_at=NOW,
                error_code=JobErrorCode.INDEXING_FAILED,
                error_detail=unsafe_detail,
            )
    with pytest.raises(ValueError, match="allow-listed"):
        _job(
            status=JobStatus.FAILED,
            attempts=1,
            started_at=NOW,
            finished_at=NOW,
            error_code="API_KEY_SUPERSECRET",
        )


def _job(**changes: object) -> Job:
    values: dict[str, object] = {
        "id": UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        "document_id": UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
        "document_revision": 1,
        "type": JobType.INDEX_DOCUMENT,
        "status": JobStatus.PENDING,
        "attempts": 0,
        "max_attempts": 3,
        "available_at": NOW,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(changes)
    return Job(**values)  # type: ignore[arg-type]
