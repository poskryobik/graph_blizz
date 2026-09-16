"""FastAPI application bootstrap."""

from fastapi import FastAPI, status
from fastapi.responses import JSONResponse

from backend.config import ApplicationSettings
from backend.observability import configure_logging
from backend.postgres import postgres_is_ready


def create_app(settings: ApplicationSettings | None = None) -> FastAPI:
    """Create the Graph Blizz HTTP application.

    Args:
        settings: Validated application settings. Defaults to environment loading.

    Returns:
        A FastAPI application whose liveness endpoint has no external dependencies.
    """
    resolved_settings = settings or ApplicationSettings()
    configure_logging(resolved_settings.logging)
    application = FastAPI(title="Graph Blizz")
    application.state.settings = resolved_settings

    @application.get("/health/live")
    async def liveness() -> dict[str, str]:
        """Report process liveness without consulting external services."""
        return {"status": "healthy"}

    @application.get("/health/ready")
    async def readiness() -> JSONResponse:
        """Report readiness based on an authenticated PostgreSQL query."""
        ready = await postgres_is_ready(resolved_settings.postgres)
        return JSONResponse(
            status_code=status.HTTP_200_OK
            if ready
            else status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "ready" if ready else "unready"},
        )

    return application


app = create_app()
