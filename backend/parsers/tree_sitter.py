"""Tree-sitter adapters for the supported source-code languages."""

import re
from dataclasses import dataclass
from itertools import pairwise
from typing import Literal

from tree_sitter import Node
from tree_sitter_language_pack import get_parser

from backend.parsers.models import ParsedChunk, ParsedDocument

type TreeSitterLanguage = Literal[
    "python", "javascript", "typescript", "tsx", "java", "go"
]

DEFAULT_CODE_CHUNK_TOKEN_LIMIT = 400
_TOKEN_RE = re.compile(r"\S+")
_CLASS_NODES = {
    "class_declaration",
    "class_definition",
    "enum_declaration",
    "interface_declaration",
    "record_declaration",
    "type_declaration",
}
_FUNCTION_NODES = {
    "arrow_function",
    "function_declaration",
    "function_definition",
    "generator_function",
    "generator_function_declaration",
}
_METHOD_NODES = {
    "constructor_declaration",
    "method_declaration",
    "method_definition",
}


@dataclass(frozen=True, slots=True)
class _Symbol:
    """A source interval carrying its nearest semantic symbol context."""

    start: int
    end: int
    name: str
    symbol_type: str
    parent: str
    children: tuple["_Symbol", ...]


@dataclass(frozen=True, slots=True)
class _Region:
    """A non-overlapping source interval with chunk metadata."""

    start: int
    end: int
    symbol: str
    symbol_type: str
    parent_symbol: str


class TreeSitterParser:
    """Parse one source language into deterministic semantic code chunks.

    Valid source is partitioned without overlap: text outside symbols uses the
    module convention ``symbol=""`` and ``symbol_type="module"``. Text inside
    a class but outside a nested method remains attributed to the class.
    """

    name = "tree_sitter"

    def __init__(
        self,
        language: TreeSitterLanguage,
        content_type: str,
        *,
        token_limit: int = DEFAULT_CODE_CHUNK_TOKEN_LIMIT,
    ) -> None:
        r"""Configure a language, MIME type, and maximum ``\S+`` tokens/chunk."""
        if not isinstance(token_limit, int) or isinstance(token_limit, bool):
            raise TypeError("token_limit must be an integer")
        if token_limit <= 0:
            raise ValueError("token_limit must be positive")
        self.language = language
        self.content_type = content_type
        self.token_limit = token_limit

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
        chunks: tuple[ParsedChunk, ...]
        if syntax_status == "syntax_error_fallback":
            metadata["fallback_parser"] = "plain_text"
        if source_name is not None:
            metadata["source_name"] = source_name

        if syntax_status == "syntax_error_fallback":
            chunks = (
                ParsedChunk(
                    content=content,
                    index=0,
                    metadata={
                        "parser": self.name,
                        "language": self.language,
                        "syntax_status": syntax_status,
                    },
                ),
            )
        else:
            chunks = self._semantic_chunks(content, tree.root_node, source_name)
        return ParsedDocument(content=content, chunks=chunks, metadata=metadata)

    def _semantic_chunks(
        self, content: str, root: Node, source_name: str | None
    ) -> tuple[ParsedChunk, ...]:
        source = content.encode("utf-8", errors="surrogatepass")
        symbols = self._collect_symbols(root, source, "", False)
        regions = self._partition(0, len(source), "", "module", "", symbols)
        chunks: list[ParsedChunk] = []

        for region in regions:
            for start, end in self._split_by_token_limit(source, region):
                chunks.append(
                    ParsedChunk(
                        content=source[start:end].decode(
                            "utf-8", errors="surrogatepass"
                        ),
                        index=len(chunks),
                        metadata={
                            "parser": self.name,
                            "syntax_status": "parsed",
                            "path": source_name or "",
                            "language": self.language,
                            "symbol": region.symbol,
                            "symbol_type": region.symbol_type,
                            "parent_symbol": region.parent_symbol,
                            "start_line": str(self._line_at(source, start)),
                            "end_line": str(self._line_at(source, max(start, end - 1))),
                        },
                    )
                )
        return tuple(chunks)

    def _collect_symbols(
        self, node: Node, source: bytes, parent: str, inside_class: bool
    ) -> tuple[_Symbol, ...]:
        found: list[_Symbol] = []
        for child in node.named_children:
            symbol_type = self._symbol_type(child, inside_class)
            if symbol_type is None:
                found.extend(self._collect_symbols(child, source, parent, inside_class))
                continue
            name = self._symbol_name(child, source)
            symbol_parent = parent
            if child.type == "method_declaration" and not symbol_parent:
                symbol_parent = self._go_receiver_name(child, source)
            children = self._collect_symbols(
                child, source, name, symbol_type == "class"
            )
            found.append(
                _Symbol(
                    child.start_byte,
                    child.end_byte,
                    name,
                    symbol_type,
                    symbol_parent,
                    children,
                )
            )
        return tuple(found)

    @staticmethod
    def _symbol_type(node: Node, inside_class: bool) -> str | None:
        if node.type in _CLASS_NODES:
            return "class"
        if node.type in _METHOD_NODES:
            return "method"
        if node.type in _FUNCTION_NODES:
            return "method" if inside_class else "function"
        return None

    @staticmethod
    def _symbol_name(node: Node, source: bytes) -> str:
        name = node.child_by_field_name("name")
        if name is None and node.type == "type_declaration":
            for child in node.named_children:
                if child.type == "type_spec":
                    name = child.child_by_field_name("name")
                    break
        if name is None and node.type == "arrow_function" and node.parent is not None:
            declarator = node.parent
            if declarator.type == "variable_declarator":
                name = declarator.child_by_field_name("name")
        if name is None:
            return ""
        return source[name.start_byte : name.end_byte].decode(
            "utf-8", errors="surrogatepass"
        )

    @staticmethod
    def _go_receiver_name(node: Node, source: bytes) -> str:
        receiver = node.child_by_field_name("receiver")
        if receiver is None:
            return ""
        pending = list(receiver.named_children)
        while pending:
            child = pending.pop(0)
            if child.type == "type_identifier":
                return source[child.start_byte : child.end_byte].decode(
                    "utf-8", errors="surrogatepass"
                )
            pending.extend(child.named_children)
        return ""

    def _partition(
        self,
        start: int,
        end: int,
        symbol: str,
        symbol_type: str,
        parent_symbol: str,
        children: tuple[_Symbol, ...],
    ) -> tuple[_Region, ...]:
        regions: list[_Region] = []
        cursor = start
        for child in children:
            if cursor < child.start:
                regions.append(
                    _Region(cursor, child.start, symbol, symbol_type, parent_symbol)
                )
            regions.extend(
                self._partition(
                    child.start,
                    child.end,
                    child.name,
                    child.symbol_type,
                    child.parent,
                    child.children,
                )
            )
            cursor = child.end
        if cursor < end or (not regions and start == end):
            regions.append(_Region(cursor, end, symbol, symbol_type, parent_symbol))
        return tuple(regions)

    def _split_by_token_limit(
        self, source: bytes, region: _Region
    ) -> tuple[tuple[int, int], ...]:
        text = source[region.start : region.end].decode("utf-8", errors="surrogatepass")
        matches = tuple(_TOKEN_RE.finditer(text))
        if len(matches) <= self.token_limit:
            return ((region.start, region.end),)

        char_cuts = [0]
        for index in range(self.token_limit, len(matches), self.token_limit):
            char_cuts.append(matches[index].start())
        char_cuts.append(len(text))
        byte_cuts = [
            region.start + len(text[:cut].encode("utf-8", errors="surrogatepass"))
            for cut in char_cuts
        ]
        return tuple(pairwise(byte_cuts))

    @staticmethod
    def _line_at(source: bytes, offset: int) -> int:
        return source.count(b"\n", 0, offset) + 1
