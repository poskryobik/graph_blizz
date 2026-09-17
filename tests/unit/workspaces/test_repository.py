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


def _row(
    *,
    name: str = "Knowledge",
    slug: str = "knowledge",
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
    assert "INSERT INTO graph_blizz.workspaces (name, slug)" in statement
    assert parameters == ("Knowledge", "knowledge")
    assert workspace.id == WORKSPACE_ID
    assert workspace.storage_key == "ws_0123456789abcdef0123456789abcdef"
    assert workspace.status is WorkspaceStatus.ACTIVE


def test_get_returns_workspace_and_missing_workspace() -> None:
    repository, connection = _repository(_row())
    workspace = asyncio.run(repository.get(WORKSPACE_ID))
    assert workspace is not None
    assert workspace.id == WORKSPACE_ID

    connection.execute.return_value.fetchone.return_value = None
    assert asyncio.run(repository.get(WORKSPACE_ID)) is None


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
