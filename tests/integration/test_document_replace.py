"""Live PostgreSQL coverage for atomic document replacement creation."""

import asyncio
from collections.abc import Iterator
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import psycopg
import pytest

from backend.config import PostgreSQLSettings
from backend.documents import DocumentRepository, DocumentSourceService, DocumentStatus
from backend.jobs import Job, JobRepository, JobStatus
from backend.parsers import ParsedDocument
from backend.worker import service as worker_service
from backend.worker.service import IndexingJobProcessor, LeasedDocumentRepository
from tests.integration.worker_db import live_worker_postgres

pytestmark = pytest.mark.integration
WORKSPACE_ID = UUID("12345678-1234-5678-1234-567812345678")
DOCUMENT_ID = UUID("aaaaaaaa-1234-5678-1234-567812345678")
SUCCESS_DOCUMENT_ID = UUID("cccccccc-1234-5678-1234-567812345678")
FAILURE_DOCUMENT_ID = UUID("dddddddd-1234-5678-1234-567812345678")


@pytest.fixture(scope="module")
def document_postgres() -> Iterator[PostgreSQLSettings]:
    yield from live_worker_postgres()


def test_changed_source_is_pending_while_old_revision_remains_active(
    document_postgres: PostgreSQLSettings,
) -> None:
    async def scenario() -> None:
        async with await _connect(document_postgres) as connection:
            await connection.execute(
                "INSERT INTO graph_blizz.workspaces (id, name, slug) "
                "VALUES (%s, 'F032', 'f032') ON CONFLICT (id) DO NOTHING",
                (WORKSPACE_ID,),
            )
            documents = DocumentRepository(connection)
            await documents.create(
                document_id=DOCUMENT_ID,
                workspace_id=WORKSPACE_ID,
                source_key="stable",
                filename="notes.md",
                source_type="text/markdown",
                object_uri="s3://bucket/revision/1",
                content_hash="old",
            )
            await documents.transition_status(
                workspace_id=WORKSPACE_ID,
                document_id=DOCUMENT_ID,
                from_status=DocumentStatus.UPLOADED,
                to_status=DocumentStatus.READY,
            )
            await documents.commit()

        objects = MagicMock()
        objects.uri.side_effect = lambda key: f"s3://bucket/{key}"
        async with await _connect(document_postgres) as connection:
            documents = DocumentRepository(connection)
            result = await DocumentSourceService(
                documents, objects
            ).upsert_for_indexing(
                jobs=JobRepository(connection),
                workspace_id=WORKSPACE_ID,
                document_id=DOCUMENT_ID,
                source_key="stable",
                filename="notes.md",
                source_type="text/markdown",
                content=b"new",
            )
            assert result.document.status is DocumentStatus.UPDATING
            assert result.document.active_revision == 1
            assert result.job is not None
            assert result.job.document_revision == 2
            assert result.job.status is JobStatus.PENDING

        async with await _connect(document_postgres) as connection:
            cursor = await connection.execute(
                "SELECT d.active_revision, d.status, "
                "(SELECT count(*) FROM graph_blizz.document_revisions r "
                " WHERE r.document_id = d.id), "
                "(SELECT count(*) FROM graph_blizz.jobs j "
                " WHERE j.document_id = d.id) "
                "FROM graph_blizz.documents d WHERE d.id = %s",
                (DOCUMENT_ID,),
            )
            assert await cursor.fetchone() == (1, "UPDATING", 2, 1)

    asyncio.run(scenario())


def test_worker_replacement_is_fenced_and_failure_keeps_old_revision(
    document_postgres: PostgreSQLSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        async with await _connect(document_postgres) as connection:
            await connection.execute(
                "UPDATE graph_blizz.jobs SET status = 'CANCELLED', "
                "finished_at = now(), updated_at = now() WHERE status = 'PENDING'"
            )
            await connection.commit()
        success_job = await _seed_running_replacement(
            document_postgres, SUCCESS_DOCUMENT_ID, "f032-success"
        )
        failure_job = await _seed_running_replacement(
            document_postgres, FAILURE_DOCUMENT_ID, "f032-failure"
        )

        async with await _connect(document_postgres) as connection:
            documents = LeasedDocumentRepository(connection, success_job)
            stale = await documents.complete_replacement_for_job(
                workspace_id=WORKSPACE_ID,
                document_id=SUCCESS_DOCUMENT_ID,
                revision=2,
                job_id=success_job.id,
                lease_owner="stale-owner",
                attempt=success_job.attempts,
            )
            assert stale is None
            stale_attempt = await documents.complete_replacement_for_job(
                workspace_id=WORKSPACE_ID,
                document_id=SUCCESS_DOCUMENT_ID,
                revision=2,
                job_id=success_job.id,
                lease_owner="worker-1",
                attempt=success_job.attempts + 1,
            )
            assert stale_attempt is None
            await connection.execute(
                "UPDATE graph_blizz.jobs SET "
                "lease_expires_at = heartbeat_at + interval '1 microsecond' "
                "WHERE id = %s",
                (success_job.id,),
            )
            stale_lease = await documents.complete_replacement_for_job(
                workspace_id=WORKSPACE_ID,
                document_id=SUCCESS_DOCUMENT_ID,
                revision=2,
                job_id=success_job.id,
                lease_owner="worker-1",
                attempt=success_job.attempts,
            )
            assert stale_lease is None
            await connection.execute(
                "UPDATE graph_blizz.jobs SET heartbeat_at = now(), "
                "lease_expires_at = now() + interval '5 minutes', updated_at = now() "
                "WHERE id = %s",
                (success_job.id,),
            )
            await documents.commit()

        async def connect(_settings: PostgreSQLSettings) -> psycopg.AsyncConnection:  # type: ignore[type-arg]
            return await _connect(document_postgres)

        monkeypatch.setattr(worker_service, "connect_postgres", connect)
        events: list[str] = []
        processor = _processor(document_postgres, events)
        await processor.process(success_job)

        assert events == [
            "read:s3://bucket/success/1",
            "read:s3://bucket/success/2",
            "delete",
            "insert",
        ]
        await _assert_state(
            document_postgres,
            SUCCESS_DOCUMENT_ID,
            active_revision=2,
            document_status="READY",
            job_status="SUCCEEDED",
        )

        processor = _processor(
            document_postgres, [], insert_error=RuntimeError("index failed")
        )
        with pytest.raises(RuntimeError, match="index failed"):
            await processor.process(failure_job)

        await _assert_state(
            document_postgres,
            FAILURE_DOCUMENT_ID,
            active_revision=1,
            document_status="UPDATING",
            job_status="RUNNING",
        )

    asyncio.run(scenario())


async def _seed_running_replacement(
    settings: PostgreSQLSettings, document_id: UUID, slug: str
) -> Job:
    object_prefix = "success" if document_id == SUCCESS_DOCUMENT_ID else "failure"
    async with await _connect(settings) as connection:
        await connection.execute(
            "INSERT INTO graph_blizz.workspaces (id, name, slug) "
            "VALUES (%s, 'F032', 'f032') ON CONFLICT (id) DO NOTHING",
            (WORKSPACE_ID,),
        )
        await connection.execute(
            "INSERT INTO graph_blizz.documents "
            "(id, workspace_id, source_key, filename, source_type, status, "
            "active_revision) VALUES (%s, %s, %s, 'notes.md', "
            "'text/markdown', 'UPDATING', 1)",
            (document_id, WORKSPACE_ID, slug),
        )
        await connection.execute(
            "INSERT INTO graph_blizz.document_revisions "
            "(document_id, revision, object_uri, content_hash) VALUES "
            "(%s, 1, %s, 'old'), (%s, 2, %s, 'new')",
            (
                document_id,
                f"s3://bucket/{object_prefix}/1",
                document_id,
                f"s3://bucket/{object_prefix}/2",
            ),
        )
        jobs = JobRepository(connection)
        pending = await jobs.create_indexing(
            document_id=document_id, document_revision=2
        )
        assert pending.status is JobStatus.PENDING
        await connection.commit()
        job = await jobs.claim_next(owner="worker-1", lease_for=timedelta(minutes=5))
        assert job is not None
        assert job.id == pending.id
        assert job.document_id == document_id
        assert job.document_revision == 2
        await connection.commit()
        return job


def _processor(
    settings: PostgreSQLSettings,
    events: list[str],
    *,
    insert_error: Exception | None = None,
) -> IndexingJobProcessor:
    parser = MagicMock()
    parser.parse.side_effect = lambda content, **_kwargs: ParsedDocument(
        content=f"parsed {content}", chunks=()
    )
    rag = SimpleNamespace(
        adelete_by_doc_id=AsyncMock(side_effect=lambda _id: events.append("delete")),
        ainsert=AsyncMock(
            side_effect=insert_error or (lambda _content: events.append("insert"))
        ),
    )
    processor = object.__new__(IndexingJobProcessor)
    processor._settings = SimpleNamespace(
        postgres=settings,
        auth=SimpleNamespace(demo_owner_id=WORKSPACE_ID),
    )
    processor._object_store = MagicMock()
    processor._object_store.get_uri.side_effect = lambda uri: (
        events.append(f"read:{uri}") or (b"old" if uri.endswith("/1") else b"new")
    )
    processor._parsers = MagicMock()
    processor._parsers.get_parser.return_value = parser
    processor._runtimes = MagicMock()
    processor._runtimes.get = AsyncMock(return_value=SimpleNamespace(rag=rag))
    return processor


async def _assert_state(
    settings: PostgreSQLSettings,
    document_id: UUID,
    *,
    active_revision: int,
    document_status: str,
    job_status: str,
) -> None:
    async with await _connect(settings) as connection:
        cursor = await connection.execute(
            "SELECT d.active_revision, d.status, j.status "
            "FROM graph_blizz.documents d JOIN graph_blizz.jobs j "
            "ON j.document_id = d.id WHERE d.id = %s",
            (document_id,),
        )
        assert await cursor.fetchone() == (
            active_revision,
            document_status,
            job_status,
        )


async def _connect(settings: PostgreSQLSettings) -> psycopg.AsyncConnection:  # type: ignore[type-arg]
    return await psycopg.AsyncConnection.connect(
        host=settings.host,
        port=settings.port,
        dbname=settings.database,
        user=settings.username,
        password=settings.password.get_secret_value(),
        sslmode=settings.ssl_mode,
    )
