"""Basic Markdown document parser."""

import re

from backend.parsers.models import ParsedChunk, ParsedDocument

_ATX_HEADING = re.compile(r"^(#{1,6})[ \t]+(.*)$")
_FENCE_OPEN = re.compile(r"^[ ]{0,3}(`{3,}|~{3,})")


class MarkdownParser:
    """Split Markdown into preamble and ATX-heading sections."""

    name = "markdown"

    def parse(
        self,
        content: str,
        *,
        source_name: str | None = None,
    ) -> ParsedDocument:
        """Preserve Markdown and create deterministic heading-based chunks."""
        sections = self._sections(content)
        chunks = tuple(
            ParsedChunk(content=text, index=index, metadata=metadata)
            for index, (text, metadata) in enumerate(sections)
        )
        document_metadata = {
            "parser": self.name,
            "content_type": "text/markdown",
        }
        if source_name is not None:
            document_metadata["source_name"] = source_name
        return ParsedDocument(
            content=content,
            chunks=chunks,
            metadata=document_metadata,
        )

    def _sections(self, content: str) -> list[tuple[str, dict[str, str]]]:
        """Return source-preserving sections while ignoring fenced headings."""
        starts: list[tuple[int, str, str]] = []
        position = 0
        fence: str | None = None
        for line in content.splitlines(keepends=True):
            matched_line = line.removesuffix("\n").removesuffix("\r")
            if fence is not None:
                closing_fence = re.compile(
                    rf"^[ ]{{0,3}}{re.escape(fence[0])}{{{len(fence)},}}[ \t]*$"
                )
                if closing_fence.match(matched_line):
                    fence = None
            else:
                opening_fence = _FENCE_OPEN.match(matched_line)
                if opening_fence:
                    fence = opening_fence.group(1)
                    position += len(line)
                    continue
                heading_match = _ATX_HEADING.match(matched_line)
                if heading_match:
                    heading_text = re.sub(
                        r"[ \t]+#+[ \t]*$", "", heading_match.group(2)
                    ).rstrip(" \t")
                    starts.append(
                        (
                            position,
                            heading_text,
                            str(len(heading_match.group(1))),
                        )
                    )
            position += len(line)

        boundaries = [start for start, _, _ in starts]
        sections: list[tuple[str, dict[str, str]]] = []
        if not boundaries or boundaries[0] > 0:
            end = boundaries[0] if boundaries else len(content)
            sections.append((content[:end], {"section": "preamble"}))
        for index, (start, heading_text, level) in enumerate(starts):
            end = starts[index + 1][0] if index + 1 < len(starts) else len(content)
            sections.append(
                (
                    content[start:end],
                    {"section": heading_text, "heading_level": level},
                )
            )
        return sections
