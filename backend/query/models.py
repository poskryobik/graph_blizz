"""Public application results for workspace queries."""

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class QuerySource:
    """Public identity and optional retrieved code location."""

    document_id: UUID
    filename: str
    revision: int | None = None
    path: str | None = None
    language: str | None = None
    symbol: str | None = None
    start_line: int | None = None
    end_line: int | None = None


@dataclass(frozen=True, slots=True)
class QueryResult:
    """Generated answer and its authorized Demo source set."""

    answer: str
    workspace_id: UUID
    request_id: UUID
    sources: tuple[QuerySource, ...]
