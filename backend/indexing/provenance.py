"""Stable LightRAG citation encoding for parser chunk provenance."""

import base64
import json
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from backend.documents import Document
from backend.parsers import ParsedChunk, ParsedDocument

_PREFIX = "graph-blizz-source:"


def code_chunk_documents(
    document: Document,
    parsed: ParsedDocument,
    *,
    revision: int | None = None,
) -> tuple[list[str], list[str], list[str]] | None:
    """Build LightRAG documents, identities, and citations for code chunks."""
    if not parsed.chunks or "language" not in parsed.metadata:
        return None
    revision = document.active_revision if revision is None else revision
    if revision is None:
        return None
    contents = [chunk.content for chunk in parsed.chunks]
    ids = [
        chunk_document_id(document.id, revision, chunk.index) for chunk in parsed.chunks
    ]
    citations = [
        encode_chunk_provenance(document, revision, chunk) for chunk in parsed.chunks
    ]
    return contents, ids, citations


async def delete_indexed_document(
    rag: Any, document: Document, parsed: ParsedDocument
) -> None:
    """Delete either parser-owned code chunks or a legacy whole document."""
    code_documents = code_chunk_documents(document, parsed)
    if code_documents is None:
        from lightrag.utils import compute_mdhash_id  # type: ignore[import-untyped]

        await rag.adelete_by_doc_id(compute_mdhash_id(parsed.content, prefix="doc-"))
        return
    _, ids, _ = code_documents
    for document_id in ids:
        await rag.adelete_by_doc_id(document_id)


async def insert_parsed_document(
    rag: Any,
    document: Document,
    parsed: ParsedDocument,
    *,
    revision: int | None = None,
) -> None:
    """Insert code chunks with provenance, retaining legacy non-code behavior."""
    code_documents = code_chunk_documents(document, parsed, revision=revision)
    if code_documents is None:
        await rag.ainsert(parsed.content)
        return
    contents, ids, citations = code_documents
    await rag.ainsert(contents, ids=ids, file_paths=citations)


def chunk_document_id(document_id: UUID, revision: int, chunk_index: int) -> str:
    """Return a deterministic LightRAG document identity for one parser chunk."""
    return f"source-{document_id}-r{revision}-c{chunk_index}"


def encode_chunk_provenance(
    document: Document, revision: int, chunk: ParsedChunk
) -> str:
    """Encode public document identity and parser metadata as citation text."""
    payload = {
        "document_id": str(document.id),
        "revision": revision,
        "path": chunk.metadata.get("path") or document.filename,
        "language": chunk.metadata.get("language"),
        "symbol": chunk.metadata.get("symbol") or None,
        "start_line": _positive_int(chunk.metadata.get("start_line")),
        "end_line": _positive_int(chunk.metadata.get("end_line")),
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    encoded = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return f"{_PREFIX}{encoded}"


def decode_chunk_provenance(value: object) -> Mapping[str, Any] | None:
    """Decode an application citation, rejecting foreign or malformed values."""
    if not isinstance(value, str) or not value.startswith(_PREFIX):
        return None
    encoded = value.removeprefix(_PREFIX)
    try:
        raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        UUID(payload["document_id"])
    except (KeyError, TypeError, ValueError):
        return None
    return payload


def _positive_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed > 0 else None
