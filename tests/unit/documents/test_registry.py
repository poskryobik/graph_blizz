"""Unit coverage for the minimal document registry."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from backend.documents import DocumentRepository, DocumentStatus

pytestmark = pytest.mark.unit

DOCUMENT_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
WORKSPACE_ID = UUID("12345678-1234-5678-1234-567812345678")
CREATED_AT = datetime(2026, 9, 17, 10, tzinfo=UTC)


def _row() -> tuple[object, ...]:
    return (
        DOCUMENT_ID,
        WORKSPACE_ID,
        "source.pdf",
        "source.pdf",
        "application/pdf",
        "s3://documents/workspace/source.pdf",
        "a" * 64,
        "UPLOADED",
        CREATED_AT,
        CREATED_AT,
        1,
    )


def _repository(
    row: tuple[object, ...] | None,
) -> tuple[DocumentRepository, AsyncMock, AsyncMock]:
    cursor = AsyncMock()
    cursor.fetchone.return_value = row
    cursor.fetchall.return_value = [] if row is None else [row]
    connection = AsyncMock()
    connection.execute.return_value = cursor
    return DocumentRepository(connection), connection, cursor


def test_create_leaves_identity_status_and_timestamps_to_database() -> None:
    repository, connection, _ = _repository(_row())

    document = asyncio.run(
        repository.create(
            workspace_id=WORKSPACE_ID,
            source_key="source.pdf",
            filename="source.pdf",
            source_type="application/pdf",
            object_uri="s3://documents/workspace/source.pdf",
            content_hash="a" * 64,
        )
    )

    statement, parameters = connection.execute.await_args.args
    insert_clause = statement.split("RETURNING", maxsplit=1)[0]
    assert "status" not in insert_clause
    assert "created_at" not in insert_clause
    assert parameters == (
        None,
        WORKSPACE_ID,
        "source.pdf",
        "source.pdf",
        "application/pdf",
        None,
        "s3://documents/workspace/source.pdf",
        "a" * 64,
    )
    assert document.status is DocumentStatus.UPLOADED


def test_create_accepts_service_generated_document_id() -> None:
    repository, connection, _ = _repository(_row())

    asyncio.run(
        repository.create(
            document_id=DOCUMENT_ID,
            workspace_id=WORKSPACE_ID,
            source_key="source.pdf",
            filename="source.pdf",
            source_type="application/pdf",
            object_uri="s3://documents/workspace/source.pdf",
            content_hash="a" * 64,
        )
    )

    statement, parameters = connection.execute.await_args.args
    assert "id, workspace_id" in statement
    assert parameters[0] == DOCUMENT_ID


def test_get_returns_document_and_missing_document() -> None:
    repository, connection, _ = _repository(_row())
    document = asyncio.run(repository.get(WORKSPACE_ID, DOCUMENT_ID))
    assert document is not None
    assert document.id == DOCUMENT_ID
    statement, parameters = connection.execute.await_args.args
    assert "WHERE d.workspace_id = %s AND d.id = %s" in statement
    assert parameters == (WORKSPACE_ID, DOCUMENT_ID)

    connection.execute.return_value.fetchone.return_value = None
    assert asyncio.run(repository.get(WORKSPACE_ID, DOCUMENT_ID)) is None


def test_list_scopes_documents_to_workspace() -> None:
    repository, connection, _ = _repository(_row())

    documents = asyncio.run(repository.list(WORKSPACE_ID))

    statement, parameters = connection.execute.await_args.args
    assert "WHERE d.workspace_id = %s" in statement
    assert "ORDER BY d.created_at, d.id" in statement
    assert parameters == (WORKSPACE_ID,)
    assert [document.id for document in documents] == [DOCUMENT_ID]


def test_transition_status_is_atomic_and_returns_updated_document() -> None:
    row = (*_row()[:7], "INDEXING", _row()[8], _row()[9], _row()[10])
    repository, connection, _ = _repository(row)

    document = asyncio.run(
        repository.transition_status(
            workspace_id=WORKSPACE_ID,
            document_id=DOCUMENT_ID,
            from_status=DocumentStatus.UPLOADED,
            to_status=DocumentStatus.INDEXING,
        )
    )

    statement, parameters = connection.execute.await_args.args
    assert "d.workspace_id = %s AND d.id = %s AND d.status = %s" in statement
    assert parameters == (
        "INDEXING",
        "INDEXING",
        WORKSPACE_ID,
        DOCUMENT_ID,
        "UPLOADED",
    )
    assert document is not None
    assert document.status is DocumentStatus.INDEXING


def test_document_status_contains_only_demo_states() -> None:
    assert {status.value for status in DocumentStatus} == {
        "UPLOADED",
        "INDEXING",
        "READY",
        "UPDATING",
        "DELETING",
        "DELETED",
        "FAILED",
    }
