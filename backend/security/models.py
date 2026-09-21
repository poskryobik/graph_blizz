"""Normalized identities, permissions, and authorized workspace context."""

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID


class PrincipalType(StrEnum):
    """Kinds of actors understood by the application security boundary."""

    USER = "user"


class Permission(StrEnum):
    """Operations that an actor may perform in a workspace."""

    WORKSPACE_READ = "workspace.read"
    WORKSPACE_MANAGE = "workspace.manage"
    WORKSPACE_DELETE = "workspace.delete"
    QUERY_EXECUTE = "query.execute"
    DOCUMENT_READ = "document.read"
    DOCUMENT_CREATE = "document.create"
    DOCUMENT_UPDATE = "document.update"
    DOCUMENT_DELETE = "document.delete"
    MEMBERS_READ = "members.read"
    MEMBERS_MANAGE = "members.manage"
    INDEX_REBUILD = "index.rebuild"
    INDEX_REPAIR = "index.repair"


ALL_OWNER_PERMISSIONS = frozenset(Permission)


@dataclass(frozen=True, slots=True)
class PrincipalContext:
    """Application identity independent of transport credentials and IdP claims."""

    principal_id: str
    principal_type: PrincipalType


@dataclass(frozen=True, slots=True)
class AuthorizedWorkspaceContext:
    """Server-resolved workspace namespace and permissions for one principal."""

    principal_id: str
    principal_type: PrincipalType
    workspace_id: UUID
    storage_key: str
    permissions: frozenset[Permission]
    index_schema_version: int = 1
    embedding_profile: str | None = None
