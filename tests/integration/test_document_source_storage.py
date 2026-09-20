"""Real PostgreSQL and MinIO coverage for document source persistence."""

import os
import shutil
import subprocess
import textwrap
from collections.abc import Iterator
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration
PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def compose_project() -> Iterator[tuple[str, dict[str, str]]]:
    """Start isolated MinIO/PostgreSQL services and apply migrations."""
    if shutil.which("docker") is None:
        pytest.skip("Docker CLI is not installed")
    for command in (["docker", "compose", "version"], ["docker", "info"]):
        if subprocess.run(command, capture_output=True, check=False).returncode != 0:
            pytest.skip("Docker Compose or daemon is unavailable")

    project = f"graph_blizz_f013_{os.getpid()}"
    environment = os.environ.copy()
    environment["GRAPH_BLIZZ_API_PORT"] = "0"
    environment["GRAPH_BLIZZ_POSTGRES__PASSWORD"] = "f013-postgres-password"
    environment["GRAPH_BLIZZ_MINIO__ACCESS_KEY"] = "f013-access-key"
    environment["GRAPH_BLIZZ_MINIO__SECRET_KEY"] = "f013-minio-password"
    try:
        _compose(project, environment, "up", "-d", "--build", "--wait", timeout=300)
        _compose(
            project,
            environment,
            "exec",
            "-T",
            "rag-api",
            "/app/.venv/bin/alembic",
            "upgrade",
            "head",
            timeout=60,
        )
        yield project, environment
    finally:
        _compose(
            project,
            environment,
            "down",
            "--timeout",
            "5",
            "--volumes",
            "--remove-orphans",
            check=False,
            timeout=120,
        )


def test_source_remains_retrievable_after_downstream_indexing_failure(
    compose_project: tuple[str, dict[str, str]],
) -> None:
    """Exercise real service boundaries and a failing indexing orchestration."""
    project, environment = compose_project
    script = textwrap.dedent(
        """
        import asyncio
        import boto3
        import psycopg
        from botocore.config import Config

        from backend.config import ApplicationSettings
        from backend.documents import DocumentRepository, DocumentSourceService
        from backend.storage import ObjectStore

        CONTENT = b"\\x00exact original source\\xff"

        async def store_then_index(service, index):
            document = await service.store(
                workspace_id=workspace_id,
                source_key="manual.bin",
                filename="manual.bin",
                source_type="application/octet-stream",
                content=CONTENT,
            )
            await index(document)

        async def failing_index(document):
            key = DocumentSourceService.object_key(workspace_id, document.id)
            assert store.get(key) == CONTENT
            raise RuntimeError("indexing failed")

        async def main():
            global workspace_id, store
            settings = ApplicationSettings()
            client = boto3.client(
                "s3",
                endpoint_url=str(settings.minio.endpoint_url).rstrip("/"),
                region_name=settings.minio.region,
                aws_access_key_id=settings.minio.access_key.get_secret_value(),
                aws_secret_access_key=settings.minio.secret_key.get_secret_value(),
                config=Config(s3={"addressing_style": "path"}),
            )
            client.create_bucket(Bucket=settings.minio.bucket)
            store = ObjectStore(settings.minio, client=client)
            async with await psycopg.AsyncConnection.connect(
                host=settings.postgres.host,
                port=settings.postgres.port,
                dbname=settings.postgres.database,
                user=settings.postgres.username,
                password=settings.postgres.password.get_secret_value(),
                sslmode=settings.postgres.ssl_mode,
            ) as connection:
                cursor = await connection.execute(
                    "INSERT INTO graph_blizz.workspaces (name, slug) "
                    "VALUES ('F013', 'f013') RETURNING id"
                )
                workspace_id = (await cursor.fetchone())[0]
                service = DocumentSourceService(DocumentRepository(connection), store)
                try:
                    await store_then_index(service, failing_index)
                except RuntimeError as error:
                    assert str(error) == "indexing failed"
                else:
                    raise AssertionError("indexing failure was not propagated")

                cursor = await connection.execute(
                    "SELECT d.id, r.object_uri, r.content_hash, d.status "
                    "FROM graph_blizz.documents AS d "
                    "JOIN graph_blizz.document_revisions AS r "
                    "ON r.document_id = d.id AND r.revision = d.active_revision "
                    "WHERE d.workspace_id = %s",
                    (workspace_id,),
                )
                document_id, object_uri, content_hash, status = await cursor.fetchone()
                key = DocumentSourceService.object_key(workspace_id, document_id)
                assert object_uri == f"s3://{settings.minio.bucket}/{key}"
                assert content_hash == "f2e0c2a42236cb4ab4922571ee11d5d7edbc519fff50bdb209db500cf0de84b0"
                assert status == "UPLOADED"
                assert store.get(key) == CONTENT

        asyncio.run(main())
        """
    )
    _compose(
        project,
        environment,
        "exec",
        "-T",
        "rag-api",
        "/app/.venv/bin/python",
        "-c",
        script,
        timeout=30,
    )


def _compose(
    project: str,
    environment: dict[str, str],
    *arguments: str,
    check: bool = True,
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    """Run Docker Compose and report service diagnostics on failure."""
    result = subprocess.run(
        ["docker", "compose", "--project-name", project, *arguments],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )
    if check and result.returncode != 0:
        pytest.fail(
            f"docker compose {' '.join(arguments)} failed:\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result
