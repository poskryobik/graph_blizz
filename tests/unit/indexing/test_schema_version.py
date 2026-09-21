"""Unit coverage for deterministic index compatibility identities."""

from backend.config import EmbeddingSettings
from backend.indexing.schema_version import (
    INDEX_SCHEMA_VERSION,
    IndexContract,
    embedding_profile_identity,
)


def test_embedding_identity_contains_only_vector_compatibility_fields() -> None:
    first = EmbeddingSettings(
        model="embedding-v1", dimension=768, normalization=True, timeout_seconds=1
    )
    operational_change = EmbeddingSettings(
        model="embedding-v1", dimension=768, normalization=True, timeout_seconds=99
    )
    incompatible_change = EmbeddingSettings(
        model="embedding-v2", dimension=768, normalization=True
    )

    assert embedding_profile_identity(first) == embedding_profile_identity(
        operational_change
    )
    assert embedding_profile_identity(first) != embedding_profile_identity(
        incompatible_change
    )


def test_namespace_changes_with_schema_or_embedding_profile() -> None:
    current = IndexContract(INDEX_SCHEMA_VERSION, "profile-a")

    assert (
        current.namespace_suffix
        != IndexContract(INDEX_SCHEMA_VERSION + 1, "profile-a").namespace_suffix
    )
    assert (
        current.namespace_suffix
        != IndexContract(INDEX_SCHEMA_VERSION, "profile-b").namespace_suffix
    )
