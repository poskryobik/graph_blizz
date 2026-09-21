"""Add parser and index compatibility metadata.

Revision ID: 20260921_0009
Revises: 20260918_0008
Create Date: 2026-09-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260921_0009"
down_revision: str | None = "20260918_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APPLICATION_SCHEMA = "graph_blizz"
DEFAULT_EMBEDDING_PROFILE = (
    '{"dimension":null,"model":"ai-sage/Giga-Embeddings-instruct-480M-0826",'
    '"normalization":true}'
)


def upgrade() -> None:
    """Backfill Demo rows with the initial compatible indexing contract."""
    op.add_column(
        "workspaces",
        sa.Column(
            "index_schema_version", sa.Integer(), server_default="1", nullable=False
        ),
        schema=APPLICATION_SCHEMA,
    )
    op.add_column(
        "workspaces",
        sa.Column(
            "embedding_profile",
            sa.Text(),
            server_default=sa.literal(DEFAULT_EMBEDDING_PROFILE),
            nullable=False,
        ),
        schema=APPLICATION_SCHEMA,
    )
    op.create_check_constraint(
        "ck_workspaces_index_schema_version_positive",
        "workspaces",
        "index_schema_version > 0",
        schema=APPLICATION_SCHEMA,
    )
    op.create_check_constraint(
        "ck_workspaces_embedding_profile_not_blank",
        "workspaces",
        "btrim(embedding_profile) <> ''",
        schema=APPLICATION_SCHEMA,
    )
    for name in ("parser_version", "chunk_schema_version", "index_schema_version"):
        op.add_column(
            "document_revisions",
            sa.Column(name, sa.Integer(), server_default="1", nullable=False),
            schema=APPLICATION_SCHEMA,
        )
        op.create_check_constraint(
            f"ck_document_revisions_{name}_positive",
            "document_revisions",
            f"{name} > 0",
            schema=APPLICATION_SCHEMA,
        )
    op.add_column(
        "document_revisions",
        sa.Column(
            "requires_reindex", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        schema=APPLICATION_SCHEMA,
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION graph_blizz.reject_document_revision_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.document_id IS DISTINCT FROM OLD.document_id
               OR NEW.revision IS DISTINCT FROM OLD.revision
               OR NEW.object_uri IS DISTINCT FROM OLD.object_uri
               OR NEW.content_hash IS DISTINCT FROM OLD.content_hash
               OR NEW.created_at IS DISTINCT FROM OLD.created_at
               OR NEW.parser_version IS DISTINCT FROM OLD.parser_version
               OR NEW.chunk_schema_version IS DISTINCT FROM OLD.chunk_schema_version
               OR NEW.index_schema_version IS DISTINCT FROM OLD.index_schema_version
            THEN
                RAISE EXCEPTION 'document revisions are immutable';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )


def downgrade() -> None:
    """Remove compatibility metadata without changing source revisions."""
    op.execute(
        """
        CREATE OR REPLACE FUNCTION graph_blizz.reject_document_revision_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'document revisions are immutable';
        END;
        $$
        """
    )
    op.drop_column("document_revisions", "requires_reindex", schema=APPLICATION_SCHEMA)
    for name in reversed(
        ("parser_version", "chunk_schema_version", "index_schema_version")
    ):
        op.drop_constraint(
            f"ck_document_revisions_{name}_positive",
            "document_revisions",
            schema=APPLICATION_SCHEMA,
            type_="check",
        )
        op.drop_column("document_revisions", name, schema=APPLICATION_SCHEMA)
    op.drop_constraint(
        "ck_workspaces_embedding_profile_not_blank",
        "workspaces",
        schema=APPLICATION_SCHEMA,
        type_="check",
    )
    op.drop_constraint(
        "ck_workspaces_index_schema_version_positive",
        "workspaces",
        schema=APPLICATION_SCHEMA,
        type_="check",
    )
    op.drop_column("workspaces", "embedding_profile", schema=APPLICATION_SCHEMA)
    op.drop_column("workspaces", "index_schema_version", schema=APPLICATION_SCHEMA)
