"""Deterministic parser selection for Demo document formats."""

from collections.abc import Mapping
from pathlib import PurePath

from backend.parsers.contracts import DocumentParser
from backend.parsers.markdown import MarkdownParser
from backend.parsers.text import PlainTextParser


class UnsupportedDocumentTypeError(ValueError):
    """Raised when no parser is registered for a document source."""


class ParserRegistry:
    """Select registered parsers by normalized MIME type or file suffix."""

    def __init__(
        self,
        *,
        media_types: Mapping[str, DocumentParser],
        extensions: Mapping[str, DocumentParser],
    ) -> None:
        """Create a registry from explicit media type and extension mappings."""
        self._media_types = {
            self._normalize_media_type(key): parser
            for key, parser in media_types.items()
        }
        self._extensions = {
            self._normalize_extension(key): parser for key, parser in extensions.items()
        }

    def get_parser(
        self,
        *,
        media_type: str | None = None,
        filename: str | None = None,
    ) -> DocumentParser:
        """Return the first matching parser or reject the unsupported source."""
        if media_type is not None:
            parser = self._media_types.get(self._normalize_media_type(media_type))
            if parser is not None:
                return parser
        if filename is not None:
            parser = self._extensions.get(PurePath(filename).suffix.lower())
            if parser is not None:
                return parser
        raise UnsupportedDocumentTypeError(
            f"unsupported document type: media_type={media_type!r}, filename={filename!r}"
        )

    @staticmethod
    def _normalize_media_type(media_type: str) -> str:
        """Normalize MIME type casing and discard optional parameters."""
        return media_type.partition(";")[0].strip().lower()

    @staticmethod
    def _normalize_extension(extension: str) -> str:
        """Normalize an extension to a lowercase dotted suffix."""
        normalized = extension.strip().lower()
        return normalized if normalized.startswith(".") else f".{normalized}"


def create_default_parser_registry() -> ParserRegistry:
    """Create the parser registry supported by the Demo application."""
    plain_text = PlainTextParser()
    markdown = MarkdownParser()
    return ParserRegistry(
        media_types={
            "text/plain": plain_text,
            "text/markdown": markdown,
        },
        extensions={
            ".txt": plain_text,
            ".md": markdown,
            ".markdown": markdown,
            ".py": plain_text,
            ".js": plain_text,
            ".jsx": plain_text,
            ".ts": plain_text,
            ".tsx": plain_text,
            ".java": plain_text,
            ".go": plain_text,
        },
    )
