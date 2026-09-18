"""Unit coverage for framework-independent indexing orchestration."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from backend.documents import Document, DocumentStatus
from backend.indexing import DocumentStateTransitionError, IndexingService
from backend.parsers import ParsedDocument
from backend.security import AuthorizedWorkspaceContext, PrincipalType

pytestmark = pytest.mark.unit

WORKSPACE_ID = UUID("12345678-1234-5678-1234-567812345678")
DOCUMENT_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


def test_index_reads_parses_and_inserts_before_ready() -> None:
    events: list[str] = []
    repository = _repository(events)
    sources = MagicMock()
    sources.read.side_effect = lambda document: events.append("read") or b"content"
    parser = MagicMock()
    parser.parse.side_effect = lambda content, **kwargs: (
        events.append("parse") or ParsedDocument(content=f"parsed {content}", chunks=())
    )
    parsers = MagicMock()
    parsers.get_parser.return_value = parser
    rag = SimpleNamespace(
        ainsert=AsyncMock(side_effect=lambda content: events.append("insert"))
    )
    runtimes = MagicMock()
    runtimes.get = AsyncMock(
        side_effect=lambda workspace: (
            events.append("runtime") or SimpleNamespace(rag=rag)
        )
    )
    service = IndexingService(repository, sources, parsers, runtimes)

    result = asyncio.run(service.index(_workspace(), _document()))

    assert events == ["INDEXING", "read", "parse", "runtime", "insert", "READY"]
    sources.read.assert_called_once()
    parsers.get_parser.assert_called_once_with(
        media_type="text/plain", filename="notes.txt"
    )
    parser.parse.assert_called_once_with("content", source_name="notes.txt")
    rag.ainsert.assert_awaited_once_with("parsed content")
    runtimes.get.assert_awaited_once_with(_workspace())
    assert result.status is DocumentStatus.READY
    assert repository.commit.await_count == 2


@pytest.mark.parametrize("failure_at", ["read", "parse", "runtime", "insert"])
def test_error_after_indexing_marks_failed_and_preserves_original(
    failure_at: str,
) -> None:
    error = RuntimeError(failure_at)
    repository = _repository([])
    sources = MagicMock()
    if failure_at == "read":
        sources.read.side_effect = error
    else:
        sources.read.return_value = b"content"
    parser = MagicMock()
    if failure_at == "parse":
        parser.parse.side_effect = error
    else:
        parser.parse.return_value = ParsedDocument(content="parsed content", chunks=())
    parsers = MagicMock()
    parsers.get_parser.return_value = parser
    rag = SimpleNamespace(
        ainsert=AsyncMock(side_effect=error if failure_at == "insert" else None)
    )
    runtimes = MagicMock()
    if failure_at == "runtime":
        runtimes.get = AsyncMock(side_effect=error)
    else:
        runtimes.get = AsyncMock(return_value=SimpleNamespace(rag=rag))
    service = IndexingService(repository, sources, parsers, runtimes)

    with pytest.raises(RuntimeError) as caught:
        asyncio.run(service.index(_workspace(), _document()))

    assert caught.value is error
    assert [
        call.kwargs["to_status"]
        for call in repository.transition_status.await_args_list
    ] == [
        DocumentStatus.INDEXING,
        DocumentStatus.FAILED,
    ]


def test_failed_transition_error_does_not_mask_insert_error() -> None:
    original = RuntimeError("insert")
    repository = _repository([])
    repository.transition_status.side_effect = [
        replace(_document(), status=DocumentStatus.INDEXING),
        RuntimeError("could not persist FAILED"),
    ]
    sources = MagicMock()
    sources.read.return_value = b"content"
    parser = MagicMock()
    parser.parse.return_value = ParsedDocument(content="content", chunks=())
    parsers = MagicMock()
    parsers.get_parser.return_value = parser
    runtimes = MagicMock()
    runtimes.get = AsyncMock(
        return_value=SimpleNamespace(
            rag=SimpleNamespace(ainsert=AsyncMock(side_effect=original))
        )
    )

    with pytest.raises(RuntimeError) as caught:
        asyncio.run(
            IndexingService(repository, sources, parsers, runtimes).index(
                _workspace(), _document()
            )
        )

    assert caught.value is original


def test_cancellation_marks_failed_without_masking_or_leaking_cleanup() -> None:
    async def scenario() -> None:
        insert_started = asyncio.Event()
        failed_started = asyncio.Event()
        release_failed = asyncio.Event()
        statuses: list[DocumentStatus] = []
        repository = MagicMock()

        async def transition_status(**kwargs: object) -> Document:
            status = kwargs["to_status"]
            assert isinstance(status, DocumentStatus)
            statuses.append(status)
            if status is DocumentStatus.FAILED:
                failed_started.set()
                await release_failed.wait()
            return replace(_document(), status=status)

        async def insert(_content: str) -> None:
            insert_started.set()
            await asyncio.Future()

        repository.transition_status = AsyncMock(side_effect=transition_status)
        repository.commit = AsyncMock()
        sources = MagicMock()
        sources.read.return_value = b"content"
        parser = MagicMock()
        parser.parse.return_value = ParsedDocument(content="content", chunks=())
        parsers = MagicMock()
        parsers.get_parser.return_value = parser
        runtimes = MagicMock()
        runtimes.get = AsyncMock(
            return_value=SimpleNamespace(rag=SimpleNamespace(ainsert=insert))
        )
        service = IndexingService(repository, sources, parsers, runtimes)
        indexing = asyncio.create_task(service.index(_workspace(), _document()))

        await insert_started.wait()
        indexing.cancel("original cancellation")
        await failed_started.wait()
        indexing.cancel("cleanup cancellation")
        await asyncio.sleep(0)
        release_failed.set()

        with pytest.raises(asyncio.CancelledError) as caught:
            await indexing

        assert caught.value.args == ("original cancellation",)
        assert statuses == [DocumentStatus.INDEXING, DocumentStatus.FAILED]
        assert not [
            task
            for task in asyncio.all_tasks()
            if task is not asyncio.current_task() and not task.done()
        ]

    asyncio.run(scenario())


def test_initial_transition_must_start_from_uploaded() -> None:
    repository = MagicMock()
    repository.transition_status = AsyncMock(return_value=None)
    service = IndexingService(repository, MagicMock(), MagicMock(), MagicMock())

    with pytest.raises(DocumentStateTransitionError):
        asyncio.run(service.index(_workspace(), _document()))

    repository.transition_status.assert_awaited_once()


def test_rejects_document_from_another_workspace_before_side_effects() -> None:
    repository = MagicMock()
    repository.transition_status = AsyncMock()
    service = IndexingService(repository, MagicMock(), MagicMock(), MagicMock())
    document = replace(
        _document(), workspace_id=UUID("99999999-9999-4999-8999-999999999999")
    )

    with pytest.raises(ValueError, match="authorized workspace"):
        asyncio.run(service.index(_workspace(), document))

    repository.transition_status.assert_not_awaited()


def _repository(events: list[str]) -> MagicMock:
    repository = MagicMock()

    async def transition_status(**kwargs: object) -> Document:
        status = kwargs["to_status"]
        assert isinstance(status, DocumentStatus)
        events.append(status.value)
        return replace(_document(), status=status)

    repository.transition_status = AsyncMock(side_effect=transition_status)
    repository.commit = AsyncMock()
    return repository


def _workspace() -> AuthorizedWorkspaceContext:
    return AuthorizedWorkspaceContext(
        principal_id="demo-owner",
        principal_type=PrincipalType.USER,
        workspace_id=WORKSPACE_ID,
        storage_key="ws_12345678123456781234567812345678",
        permissions=frozenset(),
    )


def _document() -> Document:
    now = datetime(2026, 9, 18, tzinfo=UTC)
    return Document(
        id=DOCUMENT_ID,
        workspace_id=WORKSPACE_ID,
        source_key="notes.txt",
        filename="notes.txt",
        source_type="text/plain",
        object_uri="s3://documents/source",
        content_hash="a" * 64,
        status=DocumentStatus.UPLOADED,
        created_at=now,
        updated_at=now,
    )
