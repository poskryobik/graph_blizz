"""Unit coverage for the leased worker orchestration."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from backend.jobs import Job, JobErrorCode, JobStatus, JobType
from backend.worker.runner import Worker, classify_error

pytestmark = pytest.mark.unit
NOW = datetime(2026, 9, 18, 12, tzinfo=UTC)


def test_successful_job_is_processed_and_completed_by_owner() -> None:
    job = _running_job()
    store = AsyncMock()
    store.recover_expired.return_value = []
    store.claim_next.return_value = job
    store.succeed.return_value = replace(
        job,
        status=JobStatus.SUCCEEDED,
        lease_owner=None,
        lease_expires_at=None,
        heartbeat_at=None,
        finished_at=NOW,
    )
    processor = AsyncMock()

    worked = asyncio.run(_worker(store, processor).run_once())

    assert worked is True
    processor.process.assert_awaited_once_with(job)
    store.succeed.assert_awaited_once_with(job_id=job.id, owner="worker-1")
    store.fail.assert_not_awaited()


def test_failure_is_classified_without_persisting_exception_text() -> None:
    job = _running_job()
    store = AsyncMock()
    store.recover_expired.return_value = []
    store.claim_next.return_value = job
    processor = AsyncMock()
    processor.process.side_effect = ValueError("api_key=secret")

    asyncio.run(_worker(store, processor).run_once())

    kwargs = store.fail.await_args.kwargs
    assert kwargs["error_code"] is JobErrorCode.INDEXING_FAILED
    assert "secret" not in repr(kwargs)
    store.succeed.assert_not_awaited()


def test_idle_worker_recovers_expired_leases_before_claiming() -> None:
    store = AsyncMock()
    store.recover_expired.return_value = []
    store.claim_next.return_value = None

    worked = asyncio.run(_worker(store, AsyncMock()).run_once())

    assert worked is False
    assert str(store.method_calls[0]).startswith("call.recover_expired(")
    assert str(store.method_calls[1]).startswith("call.claim_next(")


def test_stop_event_prevents_any_new_claim() -> None:
    async def scenario() -> AsyncMock:
        store = AsyncMock()
        stop = asyncio.Event()
        stop.set()
        await _worker(store, AsyncMock()).run(stop)
        return store

    store = asyncio.run(scenario())
    store.recover_expired.assert_not_awaited()
    store.claim_next.assert_not_awaited()


def test_stop_during_recovery_prevents_claim() -> None:
    async def scenario() -> AsyncMock:
        store = AsyncMock()
        stop = asyncio.Event()

        async def recover(*, retry_after: timedelta) -> list[Job]:
            assert retry_after == timedelta(seconds=5)
            stop.set()
            return []

        store.recover_expired.side_effect = recover
        await _worker(store, AsyncMock()).run(stop)
        return store

    store = asyncio.run(scenario())
    store.recover_expired.assert_awaited_once_with(retry_after=timedelta(seconds=5))
    store.claim_next.assert_not_awaited()


def test_dependency_failures_use_allow_list_code() -> None:
    assert (
        classify_error(TimeoutError("token=secret"))
        is JobErrorCode.DEPENDENCY_UNAVAILABLE
    )


def _worker(store: AsyncMock, processor: AsyncMock) -> Worker:
    return Worker(
        store,
        processor,
        owner="worker-1",
        lease_for=timedelta(minutes=1),
        heartbeat_every=10,
        retry_after=timedelta(seconds=5),
        poll_every=0.01,
    )


def _running_job() -> Job:
    return Job(
        id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        document_id=UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
        document_revision=1,
        type=JobType.INDEX_DOCUMENT,
        status=JobStatus.RUNNING,
        attempts=1,
        max_attempts=3,
        available_at=NOW,
        created_at=NOW,
        updated_at=NOW,
        lease_owner="worker-1",
        lease_expires_at=NOW + timedelta(minutes=1),
        heartbeat_at=NOW,
        started_at=NOW,
    )
