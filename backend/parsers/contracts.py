"""Contracts implemented by document parsers."""

from typing import Protocol

from backend.parsers.models import ParsedDocument


class DocumentParser(Protocol):
    """Parse source text into deterministic chunks and metadata."""

    name: str

    def parse(
        self,
        content: str,
        *,
        source_name: str | None = None,
    ) -> ParsedDocument:
        """Parse one text document without mutating its source content."""
        ...
