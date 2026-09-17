"""Internal Qdrant connectivity boundary."""

from math import ceil
from types import TracebackType
from typing import Any, Self

from qdrant_client import QdrantClient

from backend.config import QdrantSettings


class QdrantConnectionError(RuntimeError):
    """Qdrant cannot be reached through the configured endpoint."""


class QdrantConnectivity:
    """Own a Qdrant client used for internal connectivity checks."""

    def __init__(
        self,
        settings: QdrantSettings,
        *,
        client: Any | None = None,
    ) -> None:
        """Configure the client without performing a connectivity check.

        Args:
            settings: Validated Qdrant endpoint, credentials, and timeout.
            client: Optional caller-supplied client, primarily for tests.
        """
        self._client = (
            client
            if client is not None
            else QdrantClient(
                url=str(settings.url).rstrip("/"),
                api_key=(
                    settings.api_key.get_secret_value()
                    if settings.api_key is not None
                    else None
                ),
                timeout=ceil(settings.timeout_seconds),
            )
        )
        self._closed = False

    def check(self) -> bool:
        """Verify that Qdrant accepts API requests.

        Returns:
            ``True`` when Qdrant answers successfully.

        Raises:
            QdrantConnectionError: If the connectivity request fails.
        """
        try:
            self._client.get_collections()
        except Exception as error:
            raise QdrantConnectionError("Qdrant connectivity check failed") from error
        return True

    def close(self) -> None:
        """Release client resources; repeated calls are safe."""
        if not self._closed:
            self._client.close()
            self._closed = True

    def __enter__(self) -> Self:
        """Return the open adapter for a managed connectivity check."""
        return self

    def __exit__(
        self,
        _exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        """Close the owned client when leaving a managed block."""
        self.close()
