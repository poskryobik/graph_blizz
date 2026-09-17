"""Immutable parser result models."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType


def _immutable_metadata(metadata: Mapping[str, str]) -> Mapping[str, str]:
    """Validate and copy metadata into a read-only mapping."""
    copied = dict(metadata)
    if any(not isinstance(key, str) for key in copied):
        raise TypeError("metadata keys must be strings")
    if any(not key for key in copied):
        raise ValueError("metadata keys must not be empty")
    if any(not isinstance(value, str) for value in copied.values()):
        raise TypeError("metadata values must be strings")
    return MappingProxyType(copied)


@dataclass(frozen=True, slots=True)
class ParsedChunk:
    """One ordered text fragment produced by a document parser."""

    content: str
    index: int
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate the chunk and make its metadata immutable."""
        if not isinstance(self.content, str):
            raise TypeError("chunk content must be a string")
        if not isinstance(self.index, int) or isinstance(self.index, bool):
            raise TypeError("chunk index must be an integer")
        if self.index < 0:
            raise ValueError("chunk index must not be negative")
        object.__setattr__(self, "metadata", _immutable_metadata(self.metadata))


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    """Normalized output of parsing one source document."""

    content: str
    chunks: tuple[ParsedChunk, ...]
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate chunk ordering and make document metadata immutable."""
        if not isinstance(self.content, str):
            raise TypeError("document content must be a string")
        chunks = tuple(self.chunks)
        if any(not isinstance(chunk, ParsedChunk) for chunk in chunks):
            raise TypeError("document chunks must be ParsedChunk instances")
        if any(chunk.index != index for index, chunk in enumerate(chunks)):
            raise ValueError("chunk indexes must be contiguous and start at zero")
        object.__setattr__(self, "chunks", chunks)
        object.__setattr__(self, "metadata", _immutable_metadata(self.metadata))
