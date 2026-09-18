"""Workspace-authorized lifecycle registry for LightRAG runtimes."""

import asyncio
from collections.abc import Awaitable, Callable
from uuid import UUID

from backend.config import ApplicationSettings
from backend.rag.factory import LightRAGRuntime, create_lightrag_runtime
from backend.security import AuthorizedWorkspaceContext

RuntimeFactory = Callable[..., Awaitable[LightRAGRuntime]]


class LightRAGRuntimeRegistry:
    """Lazily own one LightRAG runtime per authorized workspace."""

    def __init__(
        self,
        settings: ApplicationSettings,
        *,
        runtime_factory: RuntimeFactory = create_lightrag_runtime,
    ) -> None:
        """Configure the registry without creating external resources.

        Args:
            settings: Validated settings passed to the runtime factory.
            runtime_factory: Async runtime constructor, replaceable for tests.
        """
        self._settings = settings
        self._runtime_factory = runtime_factory
        self._runtimes: dict[UUID, LightRAGRuntime] = {}
        self._lock = asyncio.Lock()
        self._closing = False
        self._close_task: asyncio.Task[None] | None = None

    async def get(self, workspace: AuthorizedWorkspaceContext) -> LightRAGRuntime:
        """Return the workspace runtime, creating it from authorized context.

        Args:
            workspace: Server-resolved workspace identity, namespace and access.

        Returns:
            The single runtime owned for this workspace in this registry.

        Raises:
            RuntimeError: If registry shutdown has started.
            Exception: If runtime initialization fails. Failed runtimes are not
                retained, so a later call can retry initialization.
        """
        if self._closing:
            raise RuntimeError("LightRAG runtime registry is closed")

        async with self._lock:
            if self._closing:
                raise RuntimeError("LightRAG runtime registry is closed")

            runtime = self._runtimes.get(workspace.workspace_id)
            if runtime is None:
                runtime = await self._runtime_factory(
                    self._settings,
                    workspace=workspace.storage_key,
                )
                self._runtimes[workspace.workspace_id] = runtime
            return runtime

    async def close(self) -> None:
        """Close every created runtime; concurrent and repeated calls are safe.

        Once this coroutine starts, new ``get`` calls are rejected. A ``get``
        already initializing under the lifecycle lock finishes first and its
        runtime is then closed. Cancellation of a close waiter does not cancel
        the shared shutdown operation.
        """
        if self._close_task is None:
            self._closing = True
            self._close_task = asyncio.create_task(self._close_all())
        await asyncio.shield(self._close_task)

    async def _close_all(self) -> None:
        async with self._lock:
            runtimes = tuple(self._runtimes.values())
            self._runtimes.clear()
            results = await asyncio.gather(
                *(runtime.close() for runtime in runtimes),
                return_exceptions=True,
            )

        for result in results:
            if isinstance(result, BaseException):
                raise result
