"""Plain text document parser."""

from backend.parsers.models import ParsedChunk, ParsedDocument


class PlainTextParser:
    """Preserve a text source as one deterministic chunk."""

    name = "plain_text"

    def parse(
        self,
        content: str,
        *,
        source_name: str | None = None,
    ) -> ParsedDocument:
        """Return the source unchanged in a single chunk."""
        document_metadata = {
            "parser": self.name,
            "content_type": "text/plain",
        }
        if source_name is not None:
            document_metadata["source_name"] = source_name

        return ParsedDocument(
            content=content,
            chunks=(
                ParsedChunk(
                    content=content,
                    index=0,
                    metadata={"parser": self.name},
                ),
            ),
            metadata=document_metadata,
        )
