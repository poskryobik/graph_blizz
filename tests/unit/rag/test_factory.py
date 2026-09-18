"""Unit coverage for production LightRAG construction and lifecycle."""

import asyncio
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar
from unittest.mock import AsyncMock

import numpy as np
import pytest
from lightrag import LightRAG as PinnedLightRAG
from lightrag.kg.postgres_impl import ClientManager

from backend.config import ApplicationSettings
from backend.rag import LightRAGConfigurationError, create_lightrag_runtime

pytestmark = pytest.mark.unit


class FakeLightRAG:
    """Capture constructor callbacks and storage environment without I/O."""

    instances: ClassVar[list["FakeLightRAG"]] = []
    initialize_error: ClassVar[BaseException | None] = None

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.environment: dict[str, str | None] = {}
        self.partial_storage = SimpleNamespace(finalize=AsyncMock())
        self.full_docs = self.partial_storage
        self._storages_status = SimpleNamespace(name="CREATED")
        self.finalize_storages = AsyncMock()
        self.instances.append(self)

    async def initialize_storages(self) -> None:
        self.environment = {
            name: os.environ.get(name)
            for name in (
                "POSTGRES_HOST",
                "POSTGRES_WORKSPACE",
                "QDRANT_URL",
                "QDRANT_WORKSPACE",
                "NEO4J_URI",
                "NEO4J_DATABASE",
                "NEO4J_WORKSPACE",
            )
        }
        if self.initialize_error is not None:
            raise self.initialize_error


class ConcurrentFakeLightRAG(FakeLightRAG):
    """Measure initialization overlap across independent event-loop threads."""

    active = 0
    max_active = 0
    guard = threading.Lock()

    async def initialize_storages(self) -> None:
        with self.guard:
            type(self).active += 1
            type(self).max_active = max(type(self).max_active, type(self).active)
        try:
            await asyncio.sleep(0.05)
            await super().initialize_storages()
        finally:
            with self.guard:
                type(self).active -= 1


class BlockingFakeLightRAG(FakeLightRAG):
    """Expose cancellation while a partially created runtime is initializing."""

    started: ClassVar[asyncio.Event | None] = None

    async def initialize_storages(self) -> None:
        assert self.started is not None
        self.started.set()
        await asyncio.Event().wait()


class SlowCleanupFakeLightRAG(BlockingFakeLightRAG):
    """Expose repeated cancellation while failed initialization is cleaning up."""

    cleanup_started: ClassVar[asyncio.Event | None] = None
    cleanup_release: ClassVar[asyncio.Event | None] = None

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.finalize_storages = AsyncMock(side_effect=self._slow_finalize_storages)

    async def _slow_finalize_storages(self) -> None:
        assert self.cleanup_started is not None
        assert self.cleanup_release is not None
        self.cleanup_started.set()
        await self.cleanup_release.wait()


class PinnedPostgresLightRAG(PinnedLightRAG):
    """Exercise pinned PostgreSQL storage initialization without external I/O."""

    async def initialize_storages(self) -> None:
        await self.full_docs.initialize()
        await self.doc_status.initialize()

    async def finalize_storages(self) -> None:
        await self.full_docs.finalize()
        await self.doc_status.finalize()


@pytest.fixture(autouse=True)
def reset_fake() -> None:
    FakeLightRAG.instances.clear()
    FakeLightRAG.initialize_error = None
    ConcurrentFakeLightRAG.active = 0
    ConcurrentFakeLightRAG.max_active = 0
    BlockingFakeLightRAG.started = None
    SlowCleanupFakeLightRAG.cleanup_started = None
    SlowCleanupFakeLightRAG.cleanup_release = None


def _settings() -> ApplicationSettings:
    return ApplicationSettings.model_validate(
        {
            "postgres": {
                "host": "postgres.internal",
                "password": "postgres-secret",
                "ssl_mode": "disable",
            },
            "qdrant": {"url": "http://qdrant.internal:6333"},
            "neo4j": {
                "uri": "bolt://neo4j.internal:7687",
                "password": "neo4j-secret",
                "database": "neo4j",
            },
            "embedding": {"dimension": 3, "model": "shared-embedding"},
            "external_llm": {"model": "external-generation"},
            "lightrag": {"working_dir": "/runtime/lightrag"},
        }
    )


def test_factory_wires_storages_namespace_and_shared_callbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    embedding = AsyncMock()
    embedding.embed_batch.return_value = [[0.1, 0.2, 0.3]]
    llm = AsyncMock()
    llm.complete.return_value = "answer"
    monkeypatch.setattr("backend.rag.factory.LightRAG", FakeLightRAG)
    monkeypatch.setenv("QDRANT_URL", "original-qdrant")

    runtime = asyncio.run(
        create_lightrag_runtime(
            _settings(),
            workspace="ws_0123456789abcdef",
            embedding_client=embedding,
            llm_client=llm,
        )
    )

    instance = FakeLightRAG.instances[0]
    expected_configuration = {
        "working_dir": str(Path("/runtime/lightrag/ws_0123456789abcdef")),
        "workspace": "ws_0123456789abcdef",
        "kv_storage": "PGKVStorage",
        "doc_status_storage": "PGDocStatusStorage",
        "vector_storage": "QdrantVectorDBStorage",
        "graph_storage": "Neo4JStorage",
        "llm_model_name": "external-generation",
        "max_parallel_insert": 4,
        "enable_llm_cache": True,
        "entity_extraction_use_json": True,
    }
    assert {
        name: instance.kwargs[name] for name in expected_configuration
    } == expected_configuration
    assert instance.kwargs["embedding_func"].embedding_dim == 3
    assert instance.kwargs["embedding_func"].model_name == "shared-embedding"
    assert instance.environment == {
        "POSTGRES_HOST": "postgres.internal",
        "POSTGRES_WORKSPACE": "",
        "QDRANT_URL": "http://qdrant.internal:6333/",
        "QDRANT_WORKSPACE": "ws_0123456789abcdef",
        "NEO4J_URI": "bolt://neo4j.internal:7687",
        "NEO4J_DATABASE": "neo4j",
        "NEO4J_WORKSPACE": "ws_0123456789abcdef",
    }
    assert os.environ["QDRANT_URL"] == "original-qdrant"

    async def call_callbacks() -> None:
        np.testing.assert_array_equal(
            await instance.kwargs["embedding_func"](["text"]),
            [[0.1, 0.2, 0.3]],
        )
        assert (
            await instance.kwargs["llm_model_func"](
                "question",
                system_prompt="system",
                history_messages=[{"role": "assistant", "content": "history"}],
                hashing_kv=object(),
            )
            == "answer"
        )
        await runtime.close()
        await runtime.close()

    asyncio.run(call_callbacks())
    embedding.embed_batch.assert_awaited_once_with(["text"])
    llm.complete.assert_awaited_once_with(
        [
            {"role": "system", "content": "system"},
            {"role": "assistant", "content": "history"},
            {"role": "user", "content": "question"},
        ]
    )
    instance.finalize_storages.assert_awaited_once()
    embedding.close.assert_awaited_once()
    llm.close.assert_awaited_once()


@pytest.mark.parametrize("workspace", ["", "space name", "../escape", "русский"])
def test_factory_rejects_non_physical_workspace_names(workspace: str) -> None:
    with pytest.raises(LightRAGConfigurationError, match="workspace"):
        asyncio.run(create_lightrag_runtime(_settings(), workspace=workspace))


def test_factory_requires_explicit_embedding_dimension() -> None:
    settings = _settings().model_copy(
        update={
            "embedding": _settings().embedding.model_copy(update={"dimension": None})
        }
    )

    with pytest.raises(LightRAGConfigurationError, match="embedding.dimension"):
        asyncio.run(create_lightrag_runtime(settings, workspace="ws_valid"))


def test_initialization_failure_closes_partial_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    embedding = AsyncMock()
    llm = AsyncMock()
    FakeLightRAG.initialize_error = RuntimeError("storage unavailable")
    monkeypatch.setattr("backend.rag.factory.LightRAG", FakeLightRAG)

    with pytest.raises(RuntimeError, match="storage unavailable"):
        asyncio.run(
            create_lightrag_runtime(
                _settings(),
                workspace="ws_valid",
                embedding_client=embedding,
                llm_client=llm,
            )
        )

    FakeLightRAG.instances[0].finalize_storages.assert_awaited_once()
    embedding.close.assert_awaited_once()
    llm.close.assert_awaited_once()


def test_initialization_cancellation_closes_partial_runtime_and_clients(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    embedding = AsyncMock()
    llm = AsyncMock()
    monkeypatch.setattr("backend.rag.factory.LightRAG", BlockingFakeLightRAG)

    async def cancel_during_initialization() -> None:
        BlockingFakeLightRAG.started = asyncio.Event()
        task = asyncio.create_task(
            create_lightrag_runtime(
                _settings(),
                workspace="ws_cancelled",
                embedding_client=embedding,
                llm_client=llm,
            )
        )
        await BlockingFakeLightRAG.started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancel_during_initialization())

    BlockingFakeLightRAG.instances[0].finalize_storages.assert_awaited_once()
    BlockingFakeLightRAG.instances[0].partial_storage.finalize.assert_awaited_once()
    embedding.close.assert_awaited_once()
    llm.close.assert_awaited_once()


def test_repeated_cancellation_waits_for_slow_initialization_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    embedding = AsyncMock()
    llm = AsyncMock()
    monkeypatch.setattr("backend.rag.factory.LightRAG", SlowCleanupFakeLightRAG)

    async def cancel_during_cleanup() -> None:
        SlowCleanupFakeLightRAG.started = asyncio.Event()
        SlowCleanupFakeLightRAG.cleanup_started = asyncio.Event()
        SlowCleanupFakeLightRAG.cleanup_release = asyncio.Event()
        task = asyncio.create_task(
            create_lightrag_runtime(
                _settings(),
                workspace="ws_repeated_cancel",
                embedding_client=embedding,
                llm_client=llm,
            )
        )
        await SlowCleanupFakeLightRAG.started.wait()
        task.cancel("initial cancellation")
        await SlowCleanupFakeLightRAG.cleanup_started.wait()
        task.cancel("repeated cancellation")
        await asyncio.sleep(0)

        assert not task.done()
        SlowCleanupFakeLightRAG.cleanup_release.set()
        with pytest.raises(asyncio.CancelledError) as cancellation:
            await task
        assert cancellation.value.args == ("initial cancellation",)

        current = asyncio.current_task()
        assert current is not None
        assert [
            pending for pending in asyncio.all_tasks() if pending is not current
        ] == []

    asyncio.run(cancel_during_cleanup())

    embedding.close.assert_awaited_once()
    llm.close.assert_awaited_once()


def test_pinned_postgres_storages_keep_distinct_live_workspaces(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    shared_db: SimpleNamespace | None = None
    references = 0

    async def get_client(_cls: type[ClientManager], **_kwargs: Any) -> SimpleNamespace:
        nonlocal shared_db, references
        if shared_db is None:
            shared_db = SimpleNamespace(workspace=os.environ["POSTGRES_WORKSPACE"])
        references += 1
        return shared_db

    async def release_client(_cls: type[ClientManager], _db: SimpleNamespace) -> None:
        nonlocal references
        references -= 1

    monkeypatch.setattr("backend.rag.factory.LightRAG", PinnedPostgresLightRAG)
    monkeypatch.setattr(
        "lightrag.lightrag.TiktokenTokenizer", lambda _model: SimpleNamespace()
    )
    monkeypatch.setattr(ClientManager, "get_client", classmethod(get_client))
    monkeypatch.setattr(ClientManager, "release_client", classmethod(release_client))

    async def create_both() -> None:
        settings = _settings().model_copy(
            update={
                "lightrag": _settings().lightrag.model_copy(
                    update={"working_dir": tmp_path}
                )
            }
        )
        first = await create_lightrag_runtime(
            settings,
            workspace="ws_first",
            embedding_client=AsyncMock(),
            llm_client=AsyncMock(),
        )
        second = await create_lightrag_runtime(
            settings,
            workspace="ws_second",
            embedding_client=AsyncMock(),
            llm_client=AsyncMock(),
        )

        assert first.rag.full_docs.workspace == "ws_first"
        assert first.rag.doc_status.workspace == "ws_first"
        assert second.rag.full_docs.workspace == "ws_second"
        assert second.rag.doc_status.workspace == "ws_second"
        assert first.rag.full_docs.db is second.rag.full_docs.db
        assert references == 4

        await first.close()
        assert references == 2
        assert second.rag.full_docs.db is shared_db
        assert second.rag.doc_status.db is shared_db
        await second.close()
        assert references == 0

    asyncio.run(create_both())


def test_second_client_constructor_failure_closes_first_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    embedding = AsyncMock()
    embedding.close.side_effect = RuntimeError("close also failed")
    monkeypatch.setattr(
        "backend.rag.factory.EmbeddingClient", lambda _settings: embedding
    )

    def fail_llm_constructor(_settings: object) -> None:
        raise RuntimeError("llm constructor failed")

    monkeypatch.setattr("backend.rag.factory.LLMClient", fail_llm_constructor)

    with pytest.raises(RuntimeError, match="llm constructor failed"):
        asyncio.run(create_lightrag_runtime(_settings(), workspace="ws_valid"))

    embedding.close.assert_awaited_once()


def test_storage_environment_is_serialized_across_event_loop_threads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("backend.rag.factory.LightRAG", ConcurrentFakeLightRAG)
    original_qdrant = os.environ.get("QDRANT_URL")

    def build(host: str, workspace: str) -> dict[str, str | None]:
        settings = _settings().model_copy(
            update={"postgres": _settings().postgres.model_copy(update={"host": host})}
        )
        embedding = AsyncMock()
        llm = AsyncMock()

        async def create_and_close() -> dict[str, str | None]:
            runtime = await create_lightrag_runtime(
                settings,
                workspace=workspace,
                embedding_client=embedding,
                llm_client=llm,
            )
            environment = runtime.rag.environment
            await runtime.close()
            return environment

        return asyncio.run(create_and_close())

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(build, "postgres.one", "ws_one")
        second = executor.submit(build, "postgres.two", "ws_two")
        environments = [first.result(), second.result()]

    assert ConcurrentFakeLightRAG.max_active == 1
    assert {
        (environment["POSTGRES_HOST"], environment["POSTGRES_WORKSPACE"])
        for environment in environments
    } == {("postgres.one", ""), ("postgres.two", "")}
    assert os.environ.get("QDRANT_URL") == original_qdrant
