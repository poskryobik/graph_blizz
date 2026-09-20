"""Integration coverage for parser-to-query source traceability."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from backend.documents import Document, DocumentStatus
from backend.indexing import IndexingService
from backend.indexing.provenance import decode_chunk_provenance
from backend.parsers import TreeSitterParser
from backend.query import QueryService
from backend.security import AuthorizedWorkspaceContext, PrincipalType

pytestmark = pytest.mark.integration
WORKSPACE_ID = UUID("12345678-1234-5678-1234-567812345678")
DOCUMENT_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


def test_code_provenance_survives_indexing_retrieval_and_query_response() -> None:
    document = _document(DocumentStatus.UPLOADED)
    repository = MagicMock()
    repository.transition_status = AsyncMock(
        side_effect=[
            replace(document, status=DocumentStatus.INDEXING),
            replace(document, status=DocumentStatus.READY),
        ]
    )
    repository.commit = AsyncMock()
    repository.list = AsyncMock(
        return_value=[replace(document, status=DocumentStatus.READY)]
    )
    sources = MagicMock()
    sources.read.return_value = b"def answer():\n    return 42\n"
    parsers = MagicMock()
    parsers.get_parser.return_value = TreeSitterParser("python", "text/x-python")
    rag = SimpleNamespace(ainsert=AsyncMock())
    runtimes = MagicMock()
    runtimes.get = AsyncMock(return_value=SimpleNamespace(rag=rag))

    asyncio.run(
        IndexingService(repository, sources, parsers, runtimes).index(
            _workspace(), document
        )
    )
    citation = rag.ainsert.await_args.kwargs["file_paths"][0]
    persisted = decode_chunk_provenance(citation)
    assert persisted is not None
    assert persisted["document_id"] == str(DOCUMENT_ID)

    rag.aquery_llm = AsyncMock(
        return_value={
            "data": {"chunks": [{"file_path": citation}]},
            "llm_response": {"content": "42", "is_streaming": False},
        }
    )
    result = asyncio.run(
        QueryService(repository, runtimes).query(_workspace(), "answer")
    )

    source = result.sources[0]
    assert source.document_id == DOCUMENT_ID
    assert source.revision == 2
    assert source.path == "src/answer.py"
    assert source.language == "python"
    assert source.symbol == "answer"
    assert source.start_line == 1
    assert source.end_line == 2


def _workspace() -> AuthorizedWorkspaceContext:
    return AuthorizedWorkspaceContext(
        principal_id="owner",
        principal_type=PrincipalType.USER,
        workspace_id=WORKSPACE_ID,
        storage_key="ws_test",
        permissions=frozenset(),
    )


def _document(status: DocumentStatus) -> Document:
    now = datetime(2026, 9, 20, tzinfo=UTC)
    return Document(
        id=DOCUMENT_ID,
        workspace_id=WORKSPACE_ID,
        source_key="src/answer.py",
        filename="src/answer.py",
        source_type="text/x-python",
        object_uri="s3://private/source",
        content_hash="a" * 64,
        status=status,
        created_at=now,
        updated_at=now,
        active_revision=2,
    )
