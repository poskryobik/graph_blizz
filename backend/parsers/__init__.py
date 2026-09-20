"""Public document parsing boundary."""

from backend.parsers.contracts import DocumentParser
from backend.parsers.markdown import MarkdownParser
from backend.parsers.models import ParsedChunk, ParsedDocument
from backend.parsers.registry import (
    BinaryDocumentTypeError,
    ParserRegistry,
    UnsupportedDocumentTypeError,
    create_default_parser_registry,
)
from backend.parsers.text import PlainTextParser
from backend.parsers.tree_sitter import TreeSitterParser

__all__ = [
    "BinaryDocumentTypeError",
    "DocumentParser",
    "MarkdownParser",
    "ParsedChunk",
    "ParsedDocument",
    "ParserRegistry",
    "PlainTextParser",
    "TreeSitterParser",
    "UnsupportedDocumentTypeError",
    "create_default_parser_registry",
]
