"""HTTP API routers."""

from backend.api.documents import router as documents_router
from backend.api.workspaces import router as workspaces_router

__all__ = ["documents_router", "workspaces_router"]
