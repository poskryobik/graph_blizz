"""Framework-independent inline document indexing service."""

import asyncio

from backend.documents import (
    Document,
    DocumentRepository,
    DocumentSourceService,
    DocumentStatus,
)
from backend.indexing.provenance import insert_parsed_document
from backend.parsers import ParserRegistry
from backend.rag import LightRAGRuntimeRegistry
from backend.security import AuthorizedWorkspaceContext


class DocumentStateTransitionError(RuntimeError):
    """The document was not in the state required by indexing."""


class IndexingService:
    """Parse a stored source and insert it into its authorized workspace runtime."""

    def __init__(
        self,
        repository: DocumentRepository,
        sources: DocumentSourceService,
        parsers: ParserRegistry,
        runtimes: LightRAGRuntimeRegistry,
    ) -> None:
        """Bind persistence, parsing, and workspace runtime boundaries."""
        self._repository = repository
        self._sources = sources
        self._parsers = parsers
        self._runtimes = runtimes

    async def index(
        self,
        workspace: AuthorizedWorkspaceContext,
        document: Document,
    ) -> Document:
        """Index one uploaded document and return its final metadata.

        Args:
            workspace: Server-authorized context selecting the LightRAG runtime.
            document: Uploaded document metadata whose original source is stored.

        Returns:
            The document metadata after transition to ``READY``.

        Raises:
            ValueError: If the document does not belong to ``workspace``.
            DocumentStateTransitionError: If an expected lifecycle transition
                cannot be applied.
            UnicodeDecodeError: If the selected text source is not UTF-8.
            Exception: The original source, parser, runtime, or insert failure.
                Once ``INDEXING`` starts, the service first attempts to persist
                ``FAILED`` without masking that original failure.
        """
        if document.workspace_id != workspace.workspace_id:
            raise ValueError("document does not belong to authorized workspace")

        indexing = await self._transition(
            document,
            from_status=DocumentStatus.UPLOADED,
            to_status=DocumentStatus.INDEXING,
        )
        try:
            source = self._sources.read(indexing).decode("utf-8")
            parser = self._parsers.get_parser(
                media_type=indexing.source_type,
                filename=indexing.filename,
            )
            parsed = parser.parse(source, source_name=indexing.filename)
            runtime = await self._runtimes.get(workspace)
            await insert_parsed_document(runtime.rag, indexing, parsed)
            return await self._transition(
                indexing,
                from_status=DocumentStatus.INDEXING,
                to_status=DocumentStatus.READY,
            )
        except asyncio.CancelledError:
            await self._mark_failed(indexing)
            raise
        except Exception:
            await self._mark_failed(indexing)
            raise

    async def _mark_failed(self, document: Document) -> None:
        """Finish a best-effort FAILED transition despite caller cancellation."""
        cleanup = asyncio.create_task(
            self._transition(
                document,
                from_status=DocumentStatus.INDEXING,
                to_status=DocumentStatus.FAILED,
            )
        )
        cleanup_result = asyncio.gather(cleanup, return_exceptions=True)
        while not cleanup_result.done():
            try:
                await asyncio.shield(cleanup_result)
            except asyncio.CancelledError:
                continue
        cleanup_result.result()

    async def _transition(
        self,
        document: Document,
        *,
        from_status: DocumentStatus,
        to_status: DocumentStatus,
    ) -> Document:
        transitioned = await self._repository.transition_status(
            workspace_id=document.workspace_id,
            document_id=document.id,
            from_status=from_status,
            to_status=to_status,
        )
        if transitioned is None:
            raise DocumentStateTransitionError(
                f"document {document.id} is not in {from_status.value} state"
            )
        await self._repository.commit()
        return transitioned
