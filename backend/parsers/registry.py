"""Deterministic parser selection for Demo document formats."""

from collections.abc import Mapping
from pathlib import PurePath

from backend.parsers.contracts import DocumentParser
from backend.parsers.markdown import MarkdownParser
from backend.parsers.text import PlainTextParser
from backend.parsers.tree_sitter import TreeSitterParser


class UnsupportedDocumentTypeError(ValueError):
    """Raised when no parser is registered for a document source."""


class BinaryDocumentTypeError(UnsupportedDocumentTypeError):
    """Raised when a binary media type must not fall back by extension."""


_BINARY_MEDIA_TYPES = {
    "application/gzip",
    "application/msword",
    "application/octet-stream",
    "application/pdf",
    "application/vnd.ms-excel",
    "application/vnd.ms-powerpoint",
    "application/x-bzip2",
    "application/x-executable",
    "application/x-tar",
    "application/zip",
    "application/x-7z-compressed",
    "application/x-rar-compressed",
}
_BINARY_MEDIA_PREFIXES = (
    "application/vnd.oasis.opendocument.",
    "application/vnd.openxmlformats-officedocument.",
    "audio/",
    "font/",
    "image/",
    "video/",
)


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
            normalized_media_type = self._normalize_media_type(media_type)
            parser = self._media_types.get(normalized_media_type)
            if parser is not None:
                return parser
            if (
                normalized_media_type in _BINARY_MEDIA_TYPES
                or normalized_media_type.startswith(_BINARY_MEDIA_PREFIXES)
            ):
                raise BinaryDocumentTypeError(
                    f"binary document type is unsupported: media_type={media_type!r}"
                )
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
    python = TreeSitterParser("python", "text/x-python")
    javascript = TreeSitterParser("javascript", "text/javascript")
    typescript = TreeSitterParser("typescript", "text/typescript")
    tsx = TreeSitterParser("tsx", "text/tsx")
    java = TreeSitterParser("java", "text/x-java-source")
    go = TreeSitterParser("go", "text/x-go")
    return ParserRegistry(
        media_types={
            "text/plain": plain_text,
            "text/markdown": markdown,
            "text/x-python": python,
            "text/python": python,
            "application/x-python-code": python,
            "text/javascript": javascript,
            "application/javascript": javascript,
            "application/x-javascript": javascript,
            "text/jsx": javascript,
            "text/typescript": typescript,
            "application/typescript": typescript,
            "application/x-typescript": typescript,
            "text/tsx": tsx,
            "application/tsx": tsx,
            "text/x-java-source": java,
            "text/x-java": java,
            "text/x-go": go,
            "text/go": go,
        },
        extensions={
            ".txt": plain_text,
            ".md": markdown,
            ".markdown": markdown,
            ".py": python,
            ".js": javascript,
            ".jsx": javascript,
            ".ts": typescript,
            ".tsx": tsx,
            ".java": java,
            ".go": go,
        },
    )
