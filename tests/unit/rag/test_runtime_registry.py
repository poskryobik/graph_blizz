"""Unit coverage for workspace runtime registry lifecycle."""

import asyncio
from typing import cast
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from backend.config import ApplicationSettings
from backend.rag import LightRAGRuntime, LightRAGRuntimeRegistry
from backend.security import AuthorizedWorkspaceContext, PrincipalType

pytestmark = pytest.mark.unit


def _context(
    workspace_id: str = "00000000-0000-0000-0000-000000000001",
    storage_key: str = "ws_server_generated_one",
) -> AuthorizedWorkspaceContext:
    return AuthorizedWorkspaceContext(
        principal_id="demo-owner",
        principal_type=PrincipalType.USER,
        workspace_id=UUID(workspace_id),
        storage_key=storage_key,
        permissions=frozenset(),
    )


def _runtime() -> LightRAGRuntime:
    return cast(LightRAGRuntime, AsyncMock(spec=LightRAGRuntime))


def test_initialization_is_lazy_and_uses_only_authorized_storage_key() -> None:
    async def scenario() -> None:
        settings = cast(ApplicationSettings, object())
        runtime = _runtime()
        factory = AsyncMock(return_value=runtime)
        registry = LightRAGRuntimeRegistry(settings, runtime_factory=factory)
        factory.assert_not_awaited()

        assert (
            await registry.get(_context(storage_key="ws_server_generated")) is runtime
        )
        factory.assert_awaited_once_with(
            settings,
            workspace="ws_server_generated",
        )
        await registry.close()

    asyncio.run(scenario())


def test_concurrent_get_creates_one_runtime_for_workspace() -> None:
    async def scenario() -> None:
        runtime = _runtime()
        entered = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def factory(
            _settings: ApplicationSettings, *, workspace: str
        ) -> LightRAGRuntime:
            nonlocal calls
            calls += 1
            assert workspace == "ws_server_generated_one"
            entered.set()
            await release.wait()
            return runtime

        registry = LightRAGRuntimeRegistry(
            cast(ApplicationSettings, object()), runtime_factory=factory
        )
        first = asyncio.create_task(registry.get(_context()))
        await entered.wait()
        second = asyncio.create_task(registry.get(_context()))
        await asyncio.sleep(0)
        release.set()

        assert await first is runtime
        assert await second is runtime
        assert calls == 1
        await registry.close()

    asyncio.run(scenario())


def test_contract_change_rotates_runtime_namespace() -> None:
    async def scenario() -> None:
        settings = cast(ApplicationSettings, object())
        runtimes = [_runtime(), _runtime(), _runtime()]
        factory = AsyncMock(side_effect=runtimes)
        registry = LightRAGRuntimeRegistry(settings, runtime_factory=factory)
        base = _context()
        first_contract = AuthorizedWorkspaceContext(
            principal_id=base.principal_id,
            principal_type=base.principal_type,
            workspace_id=base.workspace_id,
            storage_key=base.storage_key,
            permissions=base.permissions,
            index_schema_version=1,
            embedding_profile='{"model":"embedding-v1"}',
        )
        schema_change = AuthorizedWorkspaceContext(
            principal_id=base.principal_id,
            principal_type=base.principal_type,
            workspace_id=base.workspace_id,
            storage_key=base.storage_key,
            permissions=base.permissions,
            index_schema_version=2,
            embedding_profile=first_contract.embedding_profile,
        )
        profile_change = AuthorizedWorkspaceContext(
            principal_id=base.principal_id,
            principal_type=base.principal_type,
            workspace_id=base.workspace_id,
            storage_key=base.storage_key,
            permissions=base.permissions,
            index_schema_version=2,
            embedding_profile='{"model":"embedding-v2"}',
        )

        assert await registry.get(first_contract) is runtimes[0]
        assert await registry.get(first_contract) is runtimes[0]
        assert await registry.get(schema_change) is runtimes[1]
        assert await registry.get(profile_change) is runtimes[2]

        namespaces = [call.kwargs["workspace"] for call in factory.await_args_list]
        assert len(namespaces) == len(set(namespaces)) == 3
        await registry.close()

    asyncio.run(scenario())


def test_failed_and_cancelled_initialization_can_be_retried() -> None:
    async def scenario() -> None:
        runtime = _runtime()
        started = asyncio.Event()
        attempts = 0

        async def factory(
            _settings: ApplicationSettings, *, workspace: str
        ) -> LightRAGRuntime:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise ValueError("initialization failed")
            if attempts == 2:
                started.set()
                await asyncio.Event().wait()
            return runtime

        registry = LightRAGRuntimeRegistry(
            cast(ApplicationSettings, object()), runtime_factory=factory
        )
        with pytest.raises(ValueError, match="initialization failed"):
            await registry.get(_context())

        cancelled = asyncio.create_task(registry.get(_context()))
        await started.wait()
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled

        assert await registry.get(_context()) is runtime
        assert attempts == 3
        await registry.close()

    asyncio.run(scenario())


def test_close_is_idempotent_closes_all_runtimes_and_rejects_get() -> None:
    async def scenario() -> None:
        runtimes = [_runtime(), _runtime()]
        factory = AsyncMock(side_effect=runtimes)
        registry = LightRAGRuntimeRegistry(
            cast(ApplicationSettings, object()), runtime_factory=factory
        )
        await registry.get(_context())
        await registry.get(
            _context(
                "00000000-0000-0000-0000-000000000002",
                "ws_server_generated_two",
            )
        )

        await asyncio.gather(registry.close(), registry.close())
        await registry.close()

        for runtime in runtimes:
            runtime.close.assert_awaited_once()
        with pytest.raises(RuntimeError, match="registry is closed"):
            await registry.get(_context())

    asyncio.run(scenario())


def test_close_waits_for_running_get_then_closes_created_runtime() -> None:
    async def scenario() -> None:
        runtime = _runtime()
        entered = asyncio.Event()
        release = asyncio.Event()

        async def factory(
            _settings: ApplicationSettings, *, workspace: str
        ) -> LightRAGRuntime:
            entered.set()
            await release.wait()
            return runtime

        registry = LightRAGRuntimeRegistry(
            cast(ApplicationSettings, object()), runtime_factory=factory
        )
        get_task = asyncio.create_task(registry.get(_context()))
        await entered.wait()
        close_task = asyncio.create_task(registry.close())
        await asyncio.sleep(0)
        with pytest.raises(RuntimeError, match="registry is closed"):
            await registry.get(_context())
        release.set()

        assert await get_task is runtime
        await close_task
        runtime.close.assert_awaited_once()

    asyncio.run(scenario())
