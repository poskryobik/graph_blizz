"""Restart recovery contract for durable document workflows."""

import asyncio
import subprocess
import time
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import psycopg
import pytest

from backend.config import PostgreSQLSettings
from backend.jobs import Job, JobStatus
from backend.worker.runner import Worker
from backend.worker.service import PostgreSQLJobStore, connect_postgres
from tests.integration.worker_db import live_worker_postgres

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def worker_postgres() -> Iterator[PostgreSQLSettings]:
    yield from live_worker_postgres()


def test_compose_restarts_worker_after_database_outage() -> None:
    """A database outage must not leave the worker permanently stopped."""
    compose = Path("docker-compose.yml").read_text()
    worker = compose.split("  rag-worker:", 1)[1].split("\n  minio:", 1)[0]

    assert "restart: unless-stopped" in worker


def test_expired_workflow_reuses_identity_and_only_one_replacement_claims(
    worker_postgres: PostgreSQLSettings,
) -> None:
    """A database restart resumes one job without duplicating durable identity."""

    async def seed() -> tuple[UUID, UUID]:
        async with await connect_postgres(worker_postgres) as setup:
            cursor = await setup.execute(
                "WITH w AS (INSERT INTO graph_blizz.workspaces (name, slug) "
                "VALUES ('Restart recovery', 'restart-recovery') RETURNING id), "
                "d AS (INSERT INTO graph_blizz.documents "
                "(workspace_id, source_key, filename, source_type, status) "
                "SELECT id, 'restart-recovery', 'restart.txt', 'text/plain', "
                "'INDEXING' FROM w RETURNING id), r AS (INSERT INTO "
                "graph_blizz.document_revisions "
                "(document_id, revision, object_uri, content_hash) "
                "SELECT id, 1, 's3://graph-blizz/restart', 'restart-hash' FROM d), "
                "j AS (INSERT INTO graph_blizz.jobs "
                "(document_id, document_revision, status, attempts, lease_owner, "
                "lease_expires_at, heartbeat_at, started_at, available_at, "
                "created_at, updated_at) "
                "SELECT id, 1, 'RUNNING', 1, 'stopped-worker', "
                "now() - interval '1 second', now() - interval '2 seconds', "
                "now() - interval '2 seconds', now() - interval '3 seconds', "
                "now() - interval '3 seconds', now() FROM d RETURNING id) "
                "SELECT d.id, j.id FROM d CROSS JOIN j"
            )
            row = await cursor.fetchone()
            assert row is not None
            document_id, job_id = row
            await setup.commit()
            return document_id, job_id

    document_id, job_id = asyncio.run(seed())
    container = _postgres_container(worker_postgres.port)
    subprocess.run(["docker", "stop", container], check=True, capture_output=True)
    with pytest.raises(psycopg.OperationalError):
        asyncio.run(connect_postgres(worker_postgres))
    subprocess.run(["docker", "start", container], check=True, capture_output=True)
    restored_postgres = worker_postgres.model_copy(
        update={"port": _postgres_port(container)}
    )
    _wait_for_postgres(restored_postgres)

    async def recover_and_execute() -> None:
        stores = [PostgreSQLJobStore(restored_postgres) for _ in range(2)]
        recovered = await asyncio.gather(
            *(store.recover_expired(retry_after=timedelta(0)) for store in stores)
        )
        assert sum(len(batch) for batch in recovered) == 1
        stale = await stores[0].succeed(
            job_id=job_id, owner="stopped-worker", attempt=1
        )
        assert stale is None

        processors = [_RecordingProcessor(), _RecordingProcessor()]
        replacements = [
            Worker(
                store,
                processor,
                owner=f"replacement-worker-{index}",
                lease_for=timedelta(minutes=1),
                heartbeat_every=10,
                retry_after=timedelta(0),
                poll_every=0.01,
            )
            for index, (store, processor) in enumerate(zip(stores, processors))
        ]
        worked = await asyncio.gather(*(worker.run_once() for worker in replacements))
        assert sorted(worked) == [False, True]
        assert [job for processor in processors for job in processor.jobs] == [job_id]

        async with await connect_postgres(restored_postgres) as check:
            cursor = await check.execute(
                "SELECT (SELECT count(*) FROM graph_blizz.documents WHERE id = %s), "
                "(SELECT count(*) FROM graph_blizz.document_revisions "
                "WHERE document_id = %s), "
                "(SELECT count(*) FROM graph_blizz.jobs WHERE id = %s)",
                (document_id, document_id, job_id),
            )
            counts = await cursor.fetchone()
            assert counts == (1, 1, 1)
            cursor = await check.execute(
                "SELECT document_id, document_revision, status, attempts, "
                "lease_owner FROM graph_blizz.jobs WHERE id = %s",
                (job_id,),
            )
            state = await cursor.fetchone()
            assert state == (document_id, 1, JobStatus.SUCCEEDED.value, 2, None)

    asyncio.run(recover_and_execute())


class _RecordingProcessor:
    def __init__(self) -> None:
        self.jobs: list[UUID] = []

    async def process(self, job: Job) -> None:
        self.jobs.append(job.id)


def _postgres_container(port: int) -> str:
    listed = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}\t{{.Ports}}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    marker = f":{port}->5432/tcp"
    matches = [line.split("\t", 1)[0] for line in listed.splitlines() if marker in line]
    assert len(matches) == 1
    return matches[0]


def _postgres_port(container: str) -> int:
    published = subprocess.run(
        ["docker", "port", container, "5432/tcp"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return int(published.rsplit(":", 1)[1])


def _wait_for_postgres(settings: PostgreSQLSettings) -> None:
    async def connect_and_close() -> None:
        connection = await connect_postgres(settings)
        await connection.close()

    for _ in range(60):
        try:
            asyncio.run(connect_and_close())
            return
        except psycopg.OperationalError:
            pass
        time.sleep(0.25)
    pytest.fail("PostgreSQL did not become ready after restart")
