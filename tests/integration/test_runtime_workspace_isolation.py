"""Integration coverage for security context and runtime registry isolation."""

import asyncio
from typing import cast
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from backend.config import ApplicationSettings
from backend.rag import LightRAGRuntime, LightRAGRuntimeRegistry
from backend.security import AuthorizedWorkspaceContext, PrincipalType

pytestmark = pytest.mark.integration


def test_authorized_workspaces_resolve_to_isolated_runtime_namespaces() -> None:
    async def scenario() -> None:
        created_namespaces: list[str] = []
        runtimes: dict[str, LightRAGRuntime] = {}

        async def factory(
            _settings: ApplicationSettings, *, workspace: str
        ) -> LightRAGRuntime:
            created_namespaces.append(workspace)
            runtime = cast(LightRAGRuntime, AsyncMock(spec=LightRAGRuntime))
            runtimes[workspace] = runtime
            return runtime

        registry = LightRAGRuntimeRegistry(
            cast(ApplicationSettings, object()), runtime_factory=factory
        )
        first_context = AuthorizedWorkspaceContext(
            principal_id="demo-owner",
            principal_type=PrincipalType.USER,
            workspace_id=UUID("00000000-0000-0000-0000-000000000001"),
            storage_key="ws_server_generated_one",
            permissions=frozenset(),
        )
        second_context = AuthorizedWorkspaceContext(
            principal_id="demo-owner",
            principal_type=PrincipalType.USER,
            workspace_id=UUID("00000000-0000-0000-0000-000000000002"),
            storage_key="ws_server_generated_two",
            permissions=frozenset(),
        )

        first, second, first_again = await asyncio.gather(
            registry.get(first_context),
            registry.get(second_context),
            registry.get(first_context),
        )

        assert first is first_again
        assert first is not second
        assert created_namespaces == [
            "ws_server_generated_one",
            "ws_server_generated_two",
        ]
        await registry.close()
        for runtime in runtimes.values():
            runtime.close.assert_awaited_once()

    asyncio.run(scenario())
