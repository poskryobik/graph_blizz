"""OpenAI-compatible embedding boundary."""

from backend.embeddings.client import (
    EmbeddingClient,
    EmbeddingError,
    EmbeddingHTTPError,
    EmbeddingProtocolError,
    EmbeddingTimeoutError,
    EmbeddingTransportError,
)

__all__ = [
    "EmbeddingClient",
    "EmbeddingError",
    "EmbeddingHTTPError",
    "EmbeddingProtocolError",
    "EmbeddingTimeoutError",
    "EmbeddingTransportError",
]
