"""Stable identities and compatibility rules for derived index data."""

import hashlib
import json
from dataclasses import dataclass

from backend.config import EmbeddingSettings

PARSER_VERSION = 1
CHUNK_SCHEMA_VERSION = 1
INDEX_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class IndexContract:
    """Identity of data that may safely coexist in one derived index."""

    index_schema_version: int
    embedding_profile: str

    @property
    def namespace_suffix(self) -> str:
        """Return a storage-safe suffix unique to this compatibility contract."""
        digest = hashlib.sha256(self.embedding_profile.encode()).hexdigest()[:16]
        return f"i{self.index_schema_version}_{digest}"


def embedding_profile_identity(settings: EmbeddingSettings) -> str:
    """Return a deterministic identity for vector compatibility settings."""
    profile = {
        "dimension": settings.dimension,
        "model": settings.model,
        "normalization": settings.normalization,
    }
    return json.dumps(profile, separators=(",", ":"), sort_keys=True)


def active_index_contract(settings: EmbeddingSettings) -> IndexContract:
    """Build the application contract used for new indexing operations."""
    return IndexContract(INDEX_SCHEMA_VERSION, embedding_profile_identity(settings))
