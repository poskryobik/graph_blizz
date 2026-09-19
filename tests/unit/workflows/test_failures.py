"""Focused workflow failure and retry fencing coverage."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import httpx
import pytest

from backend.config import EmbeddingSettings, ExternalLLMSettings
from backend.documents import Document, DocumentStatus
from backend.embeddings import EmbeddingClient
from backend.indexing import IndexingService
from backend.jobs import Job, JobErrorCode, JobStatus, JobType
from backend.llm import LLMClient
from backend.parsers import ParsedDocument
from backend.security import AuthorizedWorkspaceContext, PrincipalType
from backend.worker.runner import Worker

pytestmark = pytest.mark.unit
NOW = datetime(2026, 9, 18, 12, tzinfo=UTC)
WORKSPACE_ID = UUID("12345678-1234-5678-1234-567812345678")
DOCUMENT_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")


@pytest.mark.parametrize(
    ("stage", "expected_code"),
    [
        ("parser", JobErrorCode.INDEXING_FAILED),
        ("embedding", JobErrorCode.DEPENDENCY_UNAVAILABLE),
        ("llm", JobErrorCode.DEPENDENCY_UNAVAILABLE),
        ("qdrant", JobErrorCode.INDEXING_FAILED),
        ("neo4j", JobErrorCode.INDEXING_FAILED),
        ("lightrag", JobErrorCode.INDEXING_FAILED),
    ],
)
def test_real_indexing_path_persists_each_stage_failure_for_exact_attempt(
    stage: str, expected_code: JobErrorCode
) -> None:
    job = _job()
    store = AsyncMock()
    store.recover_expired.return_value = []
    store.claim_next.return_value = job
    repository = _StatefulRepository(_document())
    processor = _IndexingProcessor(_indexing_service(stage, repository))

    worked = asyncio.run(_worker(store, processor).run_once())

    assert worked is True
    assert repository.document.status is DocumentStatus.FAILED
    assert repository.document.active_revision is None
    store.fail.assert_awaited_once_with(
        job_id=job.id,
        owner="worker-1",
        attempt=job.attempts,
        error_code=expected_code,
        retry_after=timedelta(seconds=5),
    )
    store.succeed.assert_not_awaited()


def test_success_is_also_fenced_to_exact_attempt() -> None:
    job = replace(_job(), attempts=2)
    store = AsyncMock()
    store.recover_expired.return_value = []
    store.claim_next.return_value = job

    asyncio.run(_worker(store, AsyncMock()).run_once())

    store.succeed.assert_awaited_once_with(job_id=job.id, owner="worker-1", attempt=2)


class _StatefulRepository:
    def __init__(self, document: Document) -> None:
        self.document = document

    async def transition_status(self, **kwargs: object) -> Document | None:
        if self.document.status is not kwargs["from_status"]:
            return None
        self.document = replace(
            self.document, status=cast(DocumentStatus, kwargs["to_status"])
        )
        return self.document

    async def commit(self) -> None:
        return None


class _IndexingProcessor:
    def __init__(self, service: IndexingService) -> None:
        self._service = service

    async def process(self, _job: Job) -> None:
        await self._service.index(_context(), _document())


class _StageRag:
    def __init__(self, stage: str) -> None:
        self._stage = stage

    async def ainsert(self, content: str) -> None:
        if self._stage == "embedding":
            await _offline_embedding().embed(content)
        elif self._stage == "llm":
            await _offline_llm().complete([{"role": "user", "content": content}])
        elif self._stage == "qdrant":
            await self._qdrant_upsert()
        elif self._stage == "neo4j":
            await self._neo4j_write()
        else:
            raise RuntimeError("LightRAG insert failed")

    async def _qdrant_upsert(self) -> None:
        raise RuntimeError("Qdrant upsert failed")

    async def _neo4j_write(self) -> None:
        raise RuntimeError("Neo4j write failed")


def _indexing_service(stage: str, repository: _StatefulRepository) -> IndexingService:
    sources = MagicMock()
    sources.read.return_value = b"source"
    parser = MagicMock()
    if stage == "parser":
        parser.parse.side_effect = RuntimeError("parser failed")
    else:
        parser.parse.return_value = ParsedDocument(content="parsed", chunks=())
    parsers = MagicMock()
    parsers.get_parser.return_value = parser
    runtimes = MagicMock()
    runtimes.get = AsyncMock(return_value=SimpleNamespace(rag=_StageRag(stage)))
    return IndexingService(repository, sources, parsers, runtimes)  # type: ignore[arg-type]


def _offline_http(_request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("offline")


def _offline_embedding() -> EmbeddingClient:
    return EmbeddingClient(
        EmbeddingSettings.model_validate(
            {"base_url": "http://embedding.invalid", "dimension": 1}
        ),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(_offline_http)),
    )


def _offline_llm() -> LLMClient:
    return LLMClient(
        ExternalLLMSettings.model_validate({"base_url": "http://llm.invalid"}),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(_offline_http)),
    )


def _worker(store: AsyncMock, processor: object) -> Worker:
    return Worker(
        store,
        processor,  # type: ignore[arg-type]
        owner="worker-1",
        lease_for=timedelta(minutes=1),
        heartbeat_every=10,
        retry_after=timedelta(seconds=5),
        poll_every=0.01,
    )


def _document() -> Document:
    return Document(
        id=DOCUMENT_ID,
        workspace_id=WORKSPACE_ID,
        source_key="source",
        filename="source.txt",
        source_type="text/plain",
        object_uri="s3://source/1",
        content_hash="hash",
        status=DocumentStatus.UPLOADED,
        created_at=NOW,
        updated_at=NOW,
        active_revision=None,
    )


def _context() -> AuthorizedWorkspaceContext:
    return AuthorizedWorkspaceContext(
        principal_id="demo-owner",
        principal_type=PrincipalType.USER,
        workspace_id=WORKSPACE_ID,
        storage_key="workspace",
        permissions=frozenset(),
    )


def _job() -> Job:
    return Job(
        id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        document_id=DOCUMENT_ID,
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
