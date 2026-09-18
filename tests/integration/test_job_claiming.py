"""Integration contract for concurrent-safe PostgreSQL job claiming."""

import asyncio
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest

from backend.config import PostgreSQLSettings
from backend.worker.service import PostgreSQLJobStore, connect_postgres
from tests.integration.worker_db import live_worker_postgres

pytestmark = pytest.mark.integration
REPOSITORY = Path("backend/jobs/repository.py")


@pytest.fixture(scope="module")
def worker_postgres() -> Iterator[PostgreSQLSettings]:
    yield from live_worker_postgres()


def test_claim_is_one_atomic_skip_locked_statement() -> None:
    sql = REPOSITORY.read_text()

    claim = sql.split("async def claim_next", 1)[1].split("async def heartbeat", 1)[0]
    assert "FOR UPDATE SKIP LOCKED" in claim
    assert "LIMIT 1" in claim
    assert "UPDATE graph_blizz.jobs" in claim
    assert "status IN ('PENDING', 'RETRY')" in claim
    assert "attempts = job.attempts + 1" in claim
    assert "lease_owner = %s" in claim


def test_owner_fences_heartbeat_and_completion() -> None:
    sql = REPOSITORY.read_text()

    heartbeat = sql.split("async def heartbeat", 1)[1].split("async def succeed", 1)[0]
    succeed = sql.split("async def succeed", 1)[1].split("async def fail", 1)[0]
    for statement in (heartbeat, succeed):
        assert "status = 'RUNNING'" in statement
        assert "lease_owner = %s" in statement
        assert "lease_expires_at > now()" in statement


def test_two_workers_cannot_claim_the_same_job(
    worker_postgres: PostgreSQLSettings,
) -> None:
    async def scenario() -> None:
        async with await connect_postgres(worker_postgres) as connection:
            await connection.execute(
                "WITH w AS (INSERT INTO graph_blizz.workspaces (name, slug) "
                "VALUES ('Claim', 'claim') RETURNING id), d AS (INSERT INTO "
                "graph_blizz.documents (workspace_id, source_key, filename, source_type) "
                "SELECT id, 'claim', 'claim.txt', 'text/plain' FROM w RETURNING id), "
                "r AS (INSERT INTO graph_blizz.document_revisions "
                "(document_id, revision, object_uri, content_hash) SELECT id, 1, "
                "'s3://graph-blizz/claim', 'hash' FROM d) "
                "INSERT INTO graph_blizz.jobs (document_id, document_revision) "
                "SELECT id, 1 FROM d"
            )
            await connection.commit()
        stores = (
            PostgreSQLJobStore(worker_postgres),
            PostgreSQLJobStore(worker_postgres),
        )
        jobs = await asyncio.gather(
            stores[0].claim_next(owner="worker-a", lease_for=timedelta(minutes=1)),
            stores[1].claim_next(owner="worker-b", lease_for=timedelta(minutes=1)),
        )
        assert sum(job is not None for job in jobs) == 1

    asyncio.run(scenario())
