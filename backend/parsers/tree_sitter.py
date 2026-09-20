"""Tree-sitter adapters for the supported source-code languages."""

from typing import Literal

from tree_sitter_language_pack import get_parser

from backend.parsers.models import ParsedChunk, ParsedDocument

type TreeSitterLanguage = Literal[
    "python", "javascript", "typescript", "tsx", "java", "go"
]


class TreeSitterParser:
    """Validate one source language with Tree-sitter and preserve its text."""

    name = "tree_sitter"

    def __init__(self, language: TreeSitterLanguage, content_type: str) -> None:
        """Configure a supported Tree-sitter language and canonical MIME type."""
        self.language = language
        self.content_type = content_type

    def parse(
        self,
        content: str,
        *,
        source_name: str | None = None,
    ) -> ParsedDocument:
        """Parse source or deterministically retain it as one fallback chunk."""
        tree = get_parser(self.language).parse(
            content.encode("utf-8", errors="surrogatepass")
        )
        syntax_status = (
            "syntax_error_fallback" if tree.root_node.has_error else "parsed"
        )
        metadata = {
            "parser": self.name,
            "content_type": self.content_type,
            "language": self.language,
            "syntax_status": syntax_status,
        }
        if syntax_status == "syntax_error_fallback":
            metadata["fallback_parser"] = "plain_text"
        if source_name is not None:
            metadata["source_name"] = source_name

        return ParsedDocument(
            content=content,
            chunks=(
                ParsedChunk(
                    content=content,
                    index=0,
                    metadata={
                        "parser": self.name,
                        "language": self.language,
                        "syntax_status": syntax_status,
                    },
                ),
            ),
            metadata=metadata,
        )
