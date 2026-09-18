"""Framework-independent Graph RAG query orchestration."""

from collections.abc import Callable
from uuid import UUID, uuid4

from lightrag import QueryParam  # type: ignore[import-untyped]

from backend.documents import DocumentRepository, DocumentStatus
from backend.query.models import QueryResult, QuerySource
from backend.rag import LightRAGRuntimeRegistry
from backend.security import AuthorizedWorkspaceContext


class QueryService:
    """Query only the runtime and documents selected by authorized context."""

    def __init__(
        self,
        repository: DocumentRepository,
        runtimes: LightRAGRuntimeRegistry,
        *,
        request_id_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        """Bind workspace-scoped persistence and runtime dependencies.

        Args:
            repository: Document metadata storage used for minimal sources.
            runtimes: Registry that resolves namespaces from authorized contexts.
            request_id_factory: Unique request identifier factory, replaceable in tests.
        """
        self._repository = repository
        self._runtimes = runtimes
        self._request_id_factory = request_id_factory

    async def query(
        self,
        workspace: AuthorizedWorkspaceContext,
        text: str,
    ) -> QueryResult:
        """Retrieve and generate an answer in one authorized workspace.

        Args:
            workspace: Server-authorized context selecting the LightRAG namespace.
            text: Non-empty natural-language query.

        Returns:
            Generated answer with a request id and READY workspace documents.

        Raises:
            ValueError: If ``text`` is empty or whitespace-only.
            TypeError: If LightRAG returns an unexpected streaming result.
            Exception: The original runtime or repository failure.
        """
        normalized = text.strip()
        if not normalized:
            raise ValueError("query text must not be empty")

        runtime = await self._runtimes.get(workspace)
        answer = await runtime.rag.aquery(normalized, param=QueryParam(stream=False))
        if not isinstance(answer, str):
            raise TypeError("query runtime returned an invalid response")

        documents = await self._repository.list(workspace.workspace_id)
        sources = tuple(
            QuerySource(document_id=document.id, filename=document.filename)
            for document in documents
            if document.workspace_id == workspace.workspace_id
            and document.status is DocumentStatus.READY
        )
        return QueryResult(
            answer=answer,
            workspace_id=workspace.workspace_id,
            request_id=self._request_id_factory(),
            sources=sources,
        )
