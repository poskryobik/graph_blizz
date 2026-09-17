"""Unit coverage for the internal Qdrant connectivity boundary."""

import pytest

from backend.config import QdrantSettings
from backend.storage import QdrantConnectionError, QdrantConnectivity

pytestmark = pytest.mark.unit


class ClientDouble:
    """Record connectivity and lifecycle calls made by the adapter."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.check_calls = 0
        self.close_calls = 0

    def get_collections(self) -> object:
        """Return a placeholder response or raise the configured error."""
        self.check_calls += 1
        if self.error is not None:
            raise self.error
        return object()

    def close(self) -> None:
        """Record resource cleanup."""
        self.close_calls += 1


def test_check_reports_success_and_context_manager_closes_client() -> None:
    client = ClientDouble()

    with QdrantConnectivity(QdrantSettings(), client=client) as connectivity:
        assert connectivity.check() is True

    assert client.check_calls == 1
    assert client.close_calls == 1


def test_check_maps_client_error_to_stable_boundary_error() -> None:
    client = ClientDouble(ConnectionError("endpoint detail"))

    with (
        QdrantConnectivity(QdrantSettings(), client=client) as connectivity,
        pytest.raises(
            QdrantConnectionError, match="Qdrant connectivity check failed"
        ) as error,
    ):
        connectivity.check()

    assert isinstance(error.value.__cause__, ConnectionError)
    assert "endpoint detail" not in str(error.value)
    assert client.close_calls == 1


def test_close_is_idempotent() -> None:
    client = ClientDouble()
    connectivity = QdrantConnectivity(QdrantSettings(), client=client)

    connectivity.close()
    connectivity.close()

    assert client.close_calls == 1
