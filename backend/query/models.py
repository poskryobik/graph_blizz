"""Public application results for workspace queries."""

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class QuerySource:
    """Minimal public identity of one indexed source document."""

    document_id: UUID
    filename: str


@dataclass(frozen=True, slots=True)
class QueryResult:
    """Generated answer and its authorized Demo source set."""

    answer: str
    workspace_id: UUID
    request_id: UUID
    sources: tuple[QuerySource, ...]
