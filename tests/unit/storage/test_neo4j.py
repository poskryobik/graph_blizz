"""Unit coverage for the internal Neo4j connectivity boundary."""

import pytest

from backend.config import Neo4jSettings
from backend.storage import Neo4jConnectionError, Neo4jConnectivity

pytestmark = pytest.mark.unit


class DriverDouble:
    """Record connectivity and lifecycle calls made by the adapter."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.check_calls = 0
        self.close_calls = 0

    def verify_connectivity(self) -> None:
        """Record a check or raise the configured error."""
        self.check_calls += 1
        if self.error is not None:
            raise self.error

    def close(self) -> None:
        """Record resource cleanup."""
        self.close_calls += 1


def test_check_reports_success_and_context_manager_closes_driver() -> None:
    driver = DriverDouble()

    with Neo4jConnectivity(Neo4jSettings(), driver=driver) as connectivity:
        assert connectivity.check() is True

    assert driver.check_calls == 1
    assert driver.close_calls == 1


def test_check_maps_driver_error_to_stable_boundary_error() -> None:
    driver = DriverDouble(ConnectionError("endpoint detail"))

    with (
        Neo4jConnectivity(Neo4jSettings(), driver=driver) as connectivity,
        pytest.raises(
            Neo4jConnectionError, match="Neo4j connectivity check failed"
        ) as error,
    ):
        connectivity.check()

    assert isinstance(error.value.__cause__, ConnectionError)
    assert "endpoint detail" not in str(error.value)
    assert driver.close_calls == 1


def test_close_is_idempotent() -> None:
    driver = DriverDouble()
    connectivity = Neo4jConnectivity(Neo4jSettings(), driver=driver)

    connectivity.close()
    connectivity.close()

    assert driver.close_calls == 1
