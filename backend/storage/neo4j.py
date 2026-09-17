"""Internal Neo4j connectivity boundary."""

from types import TracebackType
from typing import Any, Self

from neo4j import GraphDatabase

from backend.config import Neo4jSettings


class Neo4jConnectionError(RuntimeError):
    """Neo4j cannot be reached through the configured endpoint."""


class Neo4jConnectivity:
    """Own a Neo4j driver used for internal connectivity checks."""

    def __init__(
        self,
        settings: Neo4jSettings,
        *,
        driver: Any | None = None,
    ) -> None:
        """Configure the driver without performing a connectivity check.

        Args:
            settings: Validated Neo4j endpoint, credentials, and timeout.
            driver: Optional caller-supplied driver, primarily for tests.
        """
        password = (
            settings.password.get_secret_value()
            if settings.password is not None
            else None
        )
        auth = (settings.username, password) if password is not None else None
        self._driver = (
            driver
            if driver is not None
            else GraphDatabase.driver(
                str(settings.uri),
                auth=auth,
                connection_timeout=settings.connection_timeout_seconds,
            )
        )
        self._closed = False

    def check(self) -> bool:
        """Verify that Neo4j accepts authenticated Bolt requests.

        Returns:
            ``True`` when Neo4j answers successfully.

        Raises:
            Neo4jConnectionError: If the connectivity request fails.
        """
        try:
            self._driver.verify_connectivity()
        except Exception as error:
            raise Neo4jConnectionError("Neo4j connectivity check failed") from error
        return True

    def close(self) -> None:
        """Release driver resources; repeated calls are safe."""
        if not self._closed:
            self._driver.close()
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
        """Close the owned driver when leaving a managed block."""
        self.close()
