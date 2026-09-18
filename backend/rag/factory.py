"""Build LightRAG with project storage and model adapters."""

import asyncio
import os
import re
import threading
from collections.abc import AsyncIterator, Iterator, Mapping
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from types import TracebackType
from typing import Any, Self

import numpy as np
from lightrag import LightRAG  # type: ignore[import-untyped]
from lightrag.utils import EmbeddingFunc  # type: ignore[import-untyped]

from backend.config import ApplicationSettings
from backend.embeddings import EmbeddingClient
from backend.llm import LLMClient

_WORKSPACE_PATTERN = re.compile(r"[A-Za-z0-9_]+")
_ENVIRONMENT_LOCK = threading.Lock()
_FAILED_INITIALIZATION_CLEANUP_TIMEOUT_SECONDS = 10.0


class LightRAGConfigurationError(ValueError):
    """LightRAG cannot be built from the supplied application settings."""


class LightRAGRuntime:
    """Own one initialized LightRAG instance and its shared model clients."""

    def __init__(
        self,
        rag: LightRAG,
        embedding_client: EmbeddingClient,
        llm_client: LLMClient,
    ) -> None:
        """Record initialized resources owned by this runtime."""
        self.rag = rag
        self._embedding_client = embedding_client
        self._llm_client = llm_client
        self._closed = False

    async def close(self) -> None:
        """Finalize storages and model clients; repeated calls are safe."""
        if self._closed:
            return
        self._closed = True

        error: BaseException | None = None
        try:
            await self.rag.finalize_storages()
        except Exception as caught:  # noqa: BLE001 -- close remaining resources
            error = caught

        for client in (self._embedding_client, self._llm_client):
            try:
                await client.close()
            except Exception as caught:  # noqa: BLE001 -- close remaining resources
                if error is None:
                    error = caught

        if error is not None:
            raise error

    async def __aenter__(self) -> Self:
        """Return the initialized runtime."""
        return self

    async def __aexit__(
        self,
        _exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        """Release all runtime resources."""
        await self.close()


async def create_lightrag_runtime(
    settings: ApplicationSettings,
    *,
    workspace: str,
    embedding_client: EmbeddingClient | None = None,
    llm_client: LLMClient | None = None,
) -> LightRAGRuntime:
    """Create and initialize one workspace-scoped production LightRAG runtime.

    Args:
        settings: Validated storage, embedding, LLM, and LightRAG settings.
        workspace: Server-generated physical namespace. Only LightRAG-safe names
            containing ASCII letters, digits, and underscores are accepted.
        embedding_client: Optional prebuilt shared adapter, primarily for tests.
        llm_client: Optional prebuilt shared adapter, primarily for tests.

    Returns:
        Initialized runtime which owns the LightRAG instance and both clients.

    Raises:
        LightRAGConfigurationError: If workspace or embedding dimension is invalid.
        Exception: If a configured storage cannot be initialized.
    """
    _validate_configuration(settings, workspace)
    embedding: EmbeddingClient | None = None
    llm: LLMClient | None = None

    try:
        embedding = embedding_client or EmbeddingClient(settings.embedding)
        llm = llm_client or LLMClient(settings.external_llm)
    except BaseException:
        await _cleanup_after_failure(None, embedding, llm)
        raise
    assert embedding is not None
    assert llm is not None

    async def embed(texts: list[str]) -> np.ndarray[Any, np.dtype[np.float64]]:
        return np.asarray(await embedding.embed_batch(texts), dtype=np.float64)

    async def complete(
        prompt: str,
        system_prompt: str | None = None,
        history_messages: list[Mapping[str, object]] | None = None,
        **_kwargs: Any,
    ) -> str:
        messages: list[Mapping[str, object]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.extend(history_messages or [])
        messages.append({"role": "user", "content": prompt})
        return await llm.complete(messages)

    rag: LightRAG | None = None
    try:
        async with _serialized_storage_environment(settings, workspace):
            rag = LightRAG(
                working_dir=str(Path(settings.lightrag.working_dir) / workspace),
                workspace=workspace,
                kv_storage=settings.lightrag.kv_storage,
                doc_status_storage=settings.lightrag.document_status_storage,
                vector_storage=settings.lightrag.vector_storage,
                graph_storage=settings.lightrag.graph_storage,
                embedding_func=EmbeddingFunc(
                    embedding_dim=settings.embedding.dimension,
                    func=embed,
                    model_name=settings.embedding.model,
                ),
                llm_model_func=complete,
                llm_model_name=settings.external_llm.model,
                max_parallel_insert=settings.lightrag.max_parallel_insert,
                enable_llm_cache=settings.lightrag.llm_cache_enabled,
                entity_extraction_use_json=True,
            )
            await rag.initialize_storages()
    except BaseException:
        await _cleanup_after_failure(rag, embedding, llm)
        raise

    return LightRAGRuntime(rag, embedding, llm)


def _validate_configuration(settings: ApplicationSettings, workspace: str) -> None:
    if not _WORKSPACE_PATTERN.fullmatch(workspace):
        raise LightRAGConfigurationError(
            "workspace must contain only ASCII letters, digits, and underscores"
        )
    if settings.embedding.dimension is None:
        raise LightRAGConfigurationError(
            "embedding.dimension is required to initialize LightRAG vector storage"
        )


@asynccontextmanager
async def _serialized_storage_environment(
    settings: ApplicationSettings, workspace: str
) -> AsyncIterator[None]:
    """Serialize process-global LightRAG environment across loops and threads."""
    while not _ENVIRONMENT_LOCK.acquire(blocking=False):
        await asyncio.sleep(0.01)

    try:
        with _storage_environment(settings, workspace):
            yield
    finally:
        _ENVIRONMENT_LOCK.release()


@contextmanager
def _storage_environment(
    settings: ApplicationSettings, workspace: str
) -> Iterator[None]:
    password = (
        settings.postgres.password.get_secret_value()
        if settings.postgres.password is not None
        else ""
    )
    neo4j_password = (
        settings.neo4j.password.get_secret_value()
        if settings.neo4j.password is not None
        else ""
    )
    values = {
        "POSTGRES_HOST": settings.postgres.host,
        "POSTGRES_PORT": str(settings.postgres.port),
        "POSTGRES_USER": settings.postgres.username,
        "POSTGRES_PASSWORD": password,
        "POSTGRES_DATABASE": settings.postgres.database,
        "POSTGRES_MAX_CONNECTIONS": str(
            settings.postgres.pool_size + settings.postgres.max_overflow
        ),
        "POSTGRES_SSL_MODE": settings.postgres.ssl_mode,
        # PostgreSQLDB is process-wide in LightRAG 1.5.7. Leaving its workspace
        # empty preserves the explicit per-storage workspace passed to LightRAG;
        # otherwise the first live runtime overwrites every later namespace.
        "POSTGRES_WORKSPACE": "",
        "QDRANT_URL": str(settings.qdrant.url),
        "QDRANT_API_KEY": (
            settings.qdrant.api_key.get_secret_value()
            if settings.qdrant.api_key is not None
            else ""
        ),
        "QDRANT_WORKSPACE": workspace,
        "NEO4J_URI": str(settings.neo4j.uri),
        "NEO4J_USERNAME": settings.neo4j.username,
        "NEO4J_PASSWORD": neo4j_password,
        "NEO4J_DATABASE": settings.neo4j.database,
        "NEO4J_CONNECTION_TIMEOUT": str(settings.neo4j.connection_timeout_seconds),
        "NEO4J_WORKSPACE": workspace,
    }
    previous = {name: os.environ.get(name) for name in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


async def _cleanup_failed_initialization(
    rag: LightRAG | None,
    embedding: EmbeddingClient | None,
    llm: LLMClient | None,
) -> None:
    resources = [] if rag is None else [lambda: _finalize_partial_lightrag(rag)]
    resources.extend(client.close for client in (embedding, llm) if client is not None)
    await asyncio.gather(*(close() for close in resources), return_exceptions=True)


async def _finalize_partial_lightrag(rag: LightRAG) -> None:
    """Finalize storages even when pinned LightRAG never reached INITIALIZED."""
    status = getattr(rag, "_storages_status", None)
    await rag.finalize_storages()
    if getattr(status, "name", None) != "CREATED":
        return

    storage_names = (
        "full_docs",
        "text_chunks",
        "full_entities",
        "full_relations",
        "entity_chunks",
        "relation_chunks",
        "entities_vdb",
        "relationships_vdb",
        "chunks_vdb",
        "chunk_entity_relation_graph",
        "llm_response_cache",
        "doc_status",
    )
    storages = (getattr(rag, name, None) for name in storage_names)
    await asyncio.gather(
        *(storage.finalize() for storage in storages if storage is not None),
        return_exceptions=True,
    )


async def _cleanup_after_failure(
    rag: LightRAG | None,
    embedding: EmbeddingClient | None,
    llm: LLMClient | None,
) -> None:
    """Run bounded cleanup without allowing task cancellation to skip it."""
    cleanup = asyncio.create_task(_cleanup_failed_initialization(rag, embedding, llm))
    try:
        async with asyncio.timeout(_FAILED_INITIALIZATION_CLEANUP_TIMEOUT_SECONDS):
            await asyncio.shield(cleanup)
    except asyncio.CancelledError:
        await _drain_cleanup_task(cleanup)
    except TimeoutError:
        cleanup.cancel()
        await _drain_cleanup_task(cleanup)


async def _drain_cleanup_task(cleanup: asyncio.Task[None]) -> None:
    """Wait for cleanup termination despite repeated caller cancellation."""
    while not cleanup.done():
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            continue
        except BaseException:  # noqa: BLE001 -- original failure remains authoritative
            break

    if cleanup.done() and not cleanup.cancelled():
        cleanup.exception()
