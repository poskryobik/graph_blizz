"""Unit coverage for source-code query provenance."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from backend.documents import Document, DocumentStatus
from backend.indexing.provenance import encode_chunk_provenance
from backend.parsers import ParsedChunk
from backend.query import QueryService
from backend.security import AuthorizedWorkspaceContext, PrincipalType

pytestmark = pytest.mark.unit
WORKSPACE_ID = UUID("12345678-1234-5678-1234-567812345678")
DOCUMENT_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


def test_query_returns_retrieved_code_chunk_provenance() -> None:
    document = _document()
    citation = encode_chunk_provenance(
        document,
        3,
        ParsedChunk(
            "def answer():\n    return 42\n",
            0,
            {
                "path": "src/answer.py",
                "language": "python",
                "symbol": "answer",
                "start_line": "10",
                "end_line": "11",
            },
        ),
    )
    rag = SimpleNamespace(
        aquery_llm=AsyncMock(
            return_value={
                "data": {"chunks": [{"file_path": citation}]},
                "llm_response": {
                    "content": "42",
                    "is_streaming": False,
                },
            }
        )
    )
    repository = MagicMock()
    repository.list = AsyncMock(return_value=[document])
    runtimes = MagicMock()
    runtimes.get = AsyncMock(return_value=SimpleNamespace(rag=rag))

    result = asyncio.run(QueryService(repository, runtimes).query(_workspace(), "why"))

    assert result.answer == "42"
    assert len(result.sources) == 1
    assert result.sources[0].document_id == DOCUMENT_ID
    assert result.sources[0].revision == 3
    assert result.sources[0].path == "src/answer.py"
    assert result.sources[0].language == "python"
    assert result.sources[0].symbol == "answer"
    assert (result.sources[0].start_line, result.sources[0].end_line) == (10, 11)


def _workspace() -> AuthorizedWorkspaceContext:
    return AuthorizedWorkspaceContext(
        principal_id="owner",
        principal_type=PrincipalType.USER,
        workspace_id=WORKSPACE_ID,
        storage_key="ws_test",
        permissions=frozenset(),
    )


def _document() -> Document:
    now = datetime(2026, 9, 20, tzinfo=UTC)
    return Document(
        id=DOCUMENT_ID,
        workspace_id=WORKSPACE_ID,
        source_key="src/answer.py",
        filename="answer.py",
        source_type="text/x-python",
        object_uri="s3://private/source",
        content_hash="a" * 64,
        status=DocumentStatus.READY,
        created_at=now,
        updated_at=now,
        active_revision=3,
    )
