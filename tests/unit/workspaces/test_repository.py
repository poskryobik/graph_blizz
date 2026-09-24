"""Unit coverage for workspace persistence operations."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from backend.workspaces import WorkspaceRepository, WorkspaceStatus

pytestmark = pytest.mark.unit

WORKSPACE_ID = UUID("12345678-1234-5678-1234-567812345678")
CREATED_AT = datetime(2026, 9, 17, 10, tzinfo=UTC)
UPDATED_AT = datetime(2026, 9, 17, 11, tzinfo=UTC)
EMBEDDING_PROFILE = (
    '{"dimension":null,"model":"ai-sage/Giga-Embeddings-instruct-480M-0826",'
    '"normalization":true}'
)


def _row(
    *,
    name: str = "Knowledge",
    slug: str = "knowledge",
    description: str | None = None,
    updated_at: datetime = CREATED_AT,
) -> tuple[object, ...]:
    return (
        WORKSPACE_ID,
        name,
        slug,
        "ws_0123456789abcdef0123456789abcdef",
        "ACTIVE",
        CREATED_AT,
        updated_at,
        1,
        EMBEDDING_PROFILE,
        description,
    )


def _repository(
    row: tuple[object, ...] | None,
) -> tuple[WorkspaceRepository, AsyncMock]:
    cursor = AsyncMock()
    cursor.fetchone.return_value = row
    connection = AsyncMock()
    connection.execute.return_value = cursor
    return WorkspaceRepository(connection), connection


def test_create_leaves_id_storage_key_status_and_timestamps_to_database() -> None:
    repository, connection = _repository(_row())

    workspace = asyncio.run(repository.create(name="Knowledge", slug="knowledge"))

    statement, parameters = connection.execute.await_args.args
    assert "INSERT INTO graph_blizz.workspaces (name, slug, description)" in statement
    assert "storage_key" not in statement.split("RETURNING", maxsplit=1)[0]
    assert parameters == ("Knowledge", "knowledge", None)
    assert workspace.id == WORKSPACE_ID
    assert workspace.storage_key == "ws_0123456789abcdef0123456789abcdef"
    assert workspace.status is WorkspaceStatus.ACTIVE
    assert workspace.description is None


def test_create_persists_trimmed_description() -> None:
    repository, connection = _repository(
        _row(description="Architecture and source code")
    )

    workspace = asyncio.run(
        repository.create(
            name="Knowledge",
            slug="knowledge",
            description="  Architecture and source code  ",
        )
    )

    _, parameters = connection.execute.await_args.args
    assert parameters == ("Knowledge", "knowledge", "Architecture and source code")
    assert workspace.description == "Architecture and source code"


def test_create_stores_null_for_blank_description() -> None:
    repository, connection = _repository(_row())

    workspace = asyncio.run(
        repository.create(name="Knowledge", slug="knowledge", description="  \n\t ")
    )

    _, parameters = connection.execute.await_args.args
    assert parameters == ("Knowledge", "knowledge", None)
    assert workspace.description is None


def test_get_returns_persisted_description() -> None:
    repository, _ = _repository(_row(description="Architecture and source code"))

    workspace = asyncio.run(repository.get(WORKSPACE_ID))

    assert workspace is not None
    assert workspace.description == "Architecture and source code"


def test_get_returns_null_description_for_pre_migration_row() -> None:
    repository, _ = _repository(
        (
            WORKSPACE_ID,
            "Knowledge",
            "knowledge",
            "ws_0123456789abcdef0123456789abcdef",
            "ACTIVE",
            CREATED_AT,
            CREATED_AT,
        )
    )

    workspace = asyncio.run(repository.get(WORKSPACE_ID))

    assert workspace is not None
    assert workspace.description is None


def test_get_returns_workspace_and_missing_workspace() -> None:
    repository, connection = _repository(_row())
    workspace = asyncio.run(repository.get(WORKSPACE_ID))
    assert workspace is not None
    assert workspace.id == WORKSPACE_ID

    connection.execute.return_value.fetchone.return_value = None
    assert asyncio.run(repository.get(WORKSPACE_ID)) is None


def test_list_orders_workspaces_by_creation_time_and_id() -> None:
    repository, connection = _repository(_row())
    connection.execute.return_value.fetchall.return_value = [
        _row(),
        _row(name="Earlier", slug="earlier"),
    ]

    workspaces = asyncio.run(repository.list())

    assert "ORDER BY created_at, id" in connection.execute.await_args.args[0]
    assert len(connection.execute.await_args.args) == 1
    assert [workspace.name for workspace in workspaces] == ["Knowledge", "Earlier"]


def test_rename_changes_name_and_slug_without_writing_storage_key() -> None:
    repository, connection = _repository(
        _row(name="Renamed", slug="renamed", updated_at=UPDATED_AT)
    )

    workspace = asyncio.run(
        repository.rename(
            WORKSPACE_ID,
            name="Renamed",
            slug="renamed",
        )
    )

    statement, parameters = connection.execute.await_args.args
    update_clause = statement.split("RETURNING", maxsplit=1)[0]
    assert "storage_key" not in update_clause
    assert parameters == ("Renamed", "renamed", WORKSPACE_ID)
    assert workspace is not None
    assert workspace.name == "Renamed"
    assert workspace.slug == "renamed"
    assert workspace.storage_key == "ws_0123456789abcdef0123456789abcdef"
    assert workspace.updated_at == UPDATED_AT


def test_rename_returns_none_for_missing_workspace() -> None:
    repository, _ = _repository(None)

    assert (
        asyncio.run(repository.rename(WORKSPACE_ID, name="Renamed", slug="renamed"))
        is None
    )
