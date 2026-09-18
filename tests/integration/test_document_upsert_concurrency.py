"""Live PostgreSQL coverage for concurrent identical document upserts."""

import asyncio
from collections.abc import Iterator
from unittest.mock import MagicMock
from uuid import UUID

import psycopg
import pytest

from backend.config import PostgreSQLSettings
from backend.documents import (
    DocumentRepository,
    DocumentSourceService,
    DocumentUpsertAction,
)
from backend.jobs import JobRepository
from tests.integration.worker_db import live_worker_postgres

pytestmark = pytest.mark.integration
WORKSPACE_ID = UUID("12345678-1234-5678-1234-567812345678")
DOCUMENT_ID = UUID("aaaaaaaa-1234-5678-1234-567812345678")
CONTENT = b"identical concurrent source"


@pytest.fixture(scope="module")
def document_postgres() -> Iterator[PostgreSQLSettings]:
    yield from live_worker_postgres()


def test_concurrent_identical_upserts_create_one_revision_and_job(
    document_postgres: PostgreSQLSettings,
) -> None:
    async def connect() -> psycopg.AsyncConnection:  # type: ignore[type-arg]
        return await psycopg.AsyncConnection.connect(
            host=document_postgres.host,
            port=document_postgres.port,
            dbname=document_postgres.database,
            user=document_postgres.username,
            password=document_postgres.password.get_secret_value(),
            sslmode=document_postgres.ssl_mode,
        )

    async def scenario() -> None:
        async with await connect() as setup:
            await setup.execute(
                "INSERT INTO graph_blizz.workspaces (id, name, slug) "
                "VALUES (%s, 'F031', 'f031')",
                (WORKSPACE_ID,),
            )
            await setup.commit()

        object_store = MagicMock()
        object_store.uri.side_effect = lambda key: f"s3://documents/{key}"

        async def upsert() -> DocumentUpsertAction:
            async with await connect() as connection:
                documents = DocumentRepository(connection)
                result = await DocumentSourceService(
                    documents, object_store
                ).upsert_for_indexing(
                    jobs=JobRepository(connection),
                    workspace_id=WORKSPACE_ID,
                    document_id=DOCUMENT_ID,
                    source_key=f"upsert-{DOCUMENT_ID}",
                    filename="notes.md",
                    source_type="text/markdown",
                    content=CONTENT,
                )
                return result.action

        actions = await asyncio.gather(upsert(), upsert())
        assert sorted(actions) == [
            DocumentUpsertAction.CREATED,
            DocumentUpsertAction.UNCHANGED,
        ]
        object_store.put.assert_called_once()

        async with await connect() as check:
            cursor = await check.execute(
                "SELECT (SELECT count(*) FROM graph_blizz.documents WHERE id = %s), "
                "(SELECT count(*) FROM graph_blizz.document_revisions "
                "WHERE document_id = %s), "
                "(SELECT count(*) FROM graph_blizz.jobs WHERE document_id = %s)",
                (DOCUMENT_ID, DOCUMENT_ID, DOCUMENT_ID),
            )
            assert await cursor.fetchone() == (1, 1, 1)

    asyncio.run(scenario())
