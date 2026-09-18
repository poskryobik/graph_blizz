"""FastAPI application bootstrap."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, status
from fastapi.responses import JSONResponse

from backend.api import documents_router, query_router, workspaces_router
from backend.config import ApplicationSettings, AuthMode
from backend.observability import configure_logging
from backend.parsers import create_default_parser_registry
from backend.postgres import postgres_is_ready
from backend.rag import LightRAGRuntimeRegistry
from backend.security import DemoOwnerAccessPolicy, DemoOwnerIdentityResolver
from backend.storage import ObjectStore


def create_app(settings: ApplicationSettings | None = None) -> FastAPI:
    """Create the Graph Blizz HTTP application.

    Args:
        settings: Validated application settings. Defaults to environment loading.

    Returns:
        A FastAPI application whose liveness endpoint has no external dependencies.
    """
    resolved_settings = settings or ApplicationSettings()
    configure_logging(resolved_settings.logging)
    runtime_registry = LightRAGRuntimeRegistry(resolved_settings)

    @asynccontextmanager
    async def lifespan(_application: FastAPI) -> AsyncIterator[None]:
        """Close lazily created workspace runtimes during application shutdown."""
        try:
            yield
        finally:
            await runtime_registry.close()

    application = FastAPI(title="Graph Blizz", lifespan=lifespan)
    application.state.settings = resolved_settings
    if resolved_settings.auth.mode is not AuthMode.DEMO_OWNER:
        raise RuntimeError(
            f"auth mode {resolved_settings.auth.mode!s} has no configured adapters"
        )
    application.state.identity_resolver = DemoOwnerIdentityResolver(
        resolved_settings.auth.demo_owner_id
    )
    application.state.workspace_access_policy = DemoOwnerAccessPolicy(
        resolved_settings.auth.demo_owner_id
    )
    application.state.object_store = ObjectStore(resolved_settings.minio)
    application.state.parser_registry = create_default_parser_registry()
    application.state.runtime_registry = runtime_registry
    application.include_router(workspaces_router)
    application.include_router(documents_router)
    application.include_router(query_router)

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
