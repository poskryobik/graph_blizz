"""Framework-independent Graph RAG query orchestration."""

from collections.abc import Callable
from uuid import UUID, uuid4

from lightrag import QueryParam  # type: ignore[import-untyped]

from backend.documents import Document, DocumentRepository, DocumentStatus
from backend.indexing.provenance import decode_chunk_provenance
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
        param = QueryParam(stream=False)
        retrieval: object = None
        query_llm = getattr(runtime.rag, "aquery_llm", None)
        if query_llm is None:
            answer = await runtime.rag.aquery(normalized, param=param)
        else:
            retrieval = await query_llm(normalized, param=param)
            answer = _retrieval_answer(retrieval)
        if not isinstance(answer, str):
            raise TypeError("query runtime returned an invalid response")

        documents = await self._repository.list(workspace.workspace_id)
        ready = {
            document.id: document
            for document in documents
            if document.workspace_id == workspace.workspace_id
            and document.status is DocumentStatus.READY
        }
        sources = _retrieved_sources(retrieval, ready)
        if not sources:
            sources = tuple(
                QuerySource(document_id=document.id, filename=document.filename)
                for document in ready.values()
            )
        return QueryResult(
            answer=answer,
            workspace_id=workspace.workspace_id,
            request_id=self._request_id_factory(),
            sources=sources,
        )


def _retrieval_answer(retrieval: object) -> object:
    if not isinstance(retrieval, dict):
        return None
    llm_response = retrieval.get("llm_response")
    if not isinstance(llm_response, dict) or llm_response.get("is_streaming"):
        return None
    return llm_response.get("content")


def _retrieved_sources(
    retrieval: object, ready: dict[UUID, Document]
) -> tuple[QuerySource, ...]:
    if not isinstance(retrieval, dict):
        return ()
    data = retrieval.get("data")
    chunks = data.get("chunks") if isinstance(data, dict) else None
    if not isinstance(chunks, list):
        return ()
    found: list[QuerySource] = []
    seen: set[tuple[UUID, int | None, int | None, int | None]] = set()
    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        provenance = decode_chunk_provenance(chunk.get("file_path"))
        if provenance is None:
            continue
        document_id = UUID(provenance["document_id"])
        document = ready.get(document_id)
        if document is None:
            continue
        revision = provenance.get("revision")
        if revision != document.active_revision:
            continue
        start_line = provenance.get("start_line")
        end_line = provenance.get("end_line")
        key = (document_id, revision, start_line, end_line)
        if key in seen:
            continue
        seen.add(key)
        found.append(
            QuerySource(
                document_id=document_id,
                filename=document.filename,
                revision=revision,
                path=provenance.get("path"),
                language=provenance.get("language"),
                symbol=provenance.get("symbol"),
                start_line=start_line,
                end_line=end_line,
            )
        )
    return tuple(found)
