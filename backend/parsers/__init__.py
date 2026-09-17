"""Public document parsing boundary."""

from backend.parsers.contracts import DocumentParser
from backend.parsers.markdown import MarkdownParser
from backend.parsers.models import ParsedChunk, ParsedDocument
from backend.parsers.registry import (
    ParserRegistry,
    UnsupportedDocumentTypeError,
    create_default_parser_registry,
)
from backend.parsers.text import PlainTextParser

__all__ = [
    "DocumentParser",
    "MarkdownParser",
    "ParsedChunk",
    "ParsedDocument",
    "ParserRegistry",
    "PlainTextParser",
    "UnsupportedDocumentTypeError",
    "create_default_parser_registry",
]
