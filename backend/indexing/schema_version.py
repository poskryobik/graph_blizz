"""Public indexing location for the shared versioning contract."""

from backend.index_versions import (
    CHUNK_SCHEMA_VERSION,
    INDEX_SCHEMA_VERSION,
    PARSER_VERSION,
    IndexContract,
    active_index_contract,
    embedding_profile_identity,
)

__all__ = [
    "CHUNK_SCHEMA_VERSION",
    "INDEX_SCHEMA_VERSION",
    "PARSER_VERSION",
    "IndexContract",
    "active_index_contract",
    "embedding_profile_identity",
]
