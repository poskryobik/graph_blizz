"""Reusable client for an OpenAI-compatible embedding endpoint."""

import math
from collections.abc import Sequence
from types import TracebackType
from typing import Any, Self

import httpx

from backend.config import EmbeddingSettings


class EmbeddingError(RuntimeError):
    """Base class for deterministic embedding client failures."""


class EmbeddingTransportError(EmbeddingError):
    """The configured embedding endpoint could not be reached."""


class EmbeddingTimeoutError(EmbeddingTransportError):
    """The embedding request exceeded its configured timeout."""


class EmbeddingHTTPError(EmbeddingError):
    """The embedding endpoint returned a non-success status."""

    def __init__(self, status_code: int) -> None:
        """Record the failed HTTP status without exposing response content."""
        self.status_code = status_code
        super().__init__(f"Embedding endpoint returned HTTP {status_code}")


class EmbeddingProtocolError(EmbeddingError):
    """The embedding endpoint returned an invalid response contract."""


class EmbeddingClient:
    """Create embeddings through one configured OpenAI-compatible endpoint."""

    def __init__(
        self,
        settings: EmbeddingSettings,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """Configure the adapter without contacting or loading a model.

        Args:
            settings: Endpoint, model, credentials, timeout, and vector dimension.
            http_client: Optional caller-owned async client, primarily for tests.
        """
        self._settings = settings
        self._endpoint = f"{str(settings.base_url).rstrip('/')}/embeddings"
        self._owns_http_client = http_client is None
        self._headers: dict[str, str] = {}
        if settings.api_key is not None:
            self._headers["Authorization"] = (
                f"Bearer {settings.api_key.get_secret_value()}"
            )
        self._http_client = http_client or httpx.AsyncClient()

    async def embed(self, text: str) -> list[float]:
        """Return one embedding vector for a text input.

        Raises:
            EmbeddingTimeoutError: If the request times out.
            EmbeddingTransportError: If the endpoint cannot be reached.
            EmbeddingHTTPError: If the endpoint returns a non-2xx response.
            EmbeddingProtocolError: If the response contract is malformed.
        """
        return (await self.embed_batch([text]))[0]

    async def embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        """Return vectors in the same order as the supplied text batch.

        Empty input returns immediately without an HTTP request.

        Raises:
            EmbeddingTimeoutError: If the request times out.
            EmbeddingTransportError: If the endpoint cannot be reached.
            EmbeddingHTTPError: If the endpoint returns a non-2xx response.
            EmbeddingProtocolError: If indices, count, vectors, or dimensions are invalid.
        """
        inputs = list(texts)
        if not inputs:
            return []

        try:
            response = await self._http_client.post(
                self._endpoint,
                json={"model": self._settings.model, "input": inputs},
                headers=self._headers,
                timeout=self._settings.timeout_seconds,
            )
        except httpx.TimeoutException as error:
            raise EmbeddingTimeoutError("Embedding request timed out") from error
        except httpx.TransportError as error:
            raise EmbeddingTransportError("Embedding transport failed") from error

        if not response.is_success:
            raise EmbeddingHTTPError(response.status_code)

        try:
            payload: Any = response.json()
        except ValueError as error:
            raise EmbeddingProtocolError(
                "Embedding response is not valid JSON"
            ) from error
        return self._parse_vectors(payload, len(inputs))

    def _parse_vectors(self, payload: Any, expected_count: int) -> list[list[float]]:
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise EmbeddingProtocolError("Embedding response must contain a data list")

        data = payload["data"]
        if len(data) != expected_count:
            raise EmbeddingProtocolError(
                "Embedding response count does not match input"
            )

        vectors: list[list[float] | None] = [None] * expected_count
        for item in data:
            if not isinstance(item, dict):
                raise EmbeddingProtocolError("Embedding result must be an object")
            index = item.get("index")
            if (
                not isinstance(index, int)
                or isinstance(index, bool)
                or index < 0
                or index >= expected_count
                or vectors[index] is not None
            ):
                raise EmbeddingProtocolError("Embedding result index is invalid")
            raw_vector = item.get("embedding")
            if not isinstance(raw_vector, list) or not all(
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(value)
                for value in raw_vector
            ):
                raise EmbeddingProtocolError("Embedding vector is invalid")
            if (
                self._settings.dimension is not None
                and len(raw_vector) != self._settings.dimension
            ):
                raise EmbeddingProtocolError("Embedding vector dimension is invalid")
            vectors[index] = [float(value) for value in raw_vector]

        if any(vector is None for vector in vectors):
            raise EmbeddingProtocolError("Embedding result indices are incomplete")
        return [vector for vector in vectors if vector is not None]

    async def close(self) -> None:
        """Close the internally owned HTTP client; caller-owned clients stay open."""
        if self._owns_http_client:
            await self._http_client.aclose()

    async def __aenter__(self) -> Self:
        """Return the configured client for an async managed block."""
        return self

    async def __aexit__(
        self,
        _exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        """Release the internally owned HTTP client."""
        await self.close()
