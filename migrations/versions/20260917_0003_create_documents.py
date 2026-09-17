"""Create the minimal document registry.

Revision ID: 20260917_0003
Revises: 20260917_0002
Create Date: 2026-09-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260917_0003"
down_revision: str | None = "20260917_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APPLICATION_SCHEMA = "graph_blizz"


def upgrade() -> None:
    """Create document metadata with workspace-local source identity."""
    op.create_table(
        "documents",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("source_key", sa.Text(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("object_uri", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Text(),
            server_default=sa.text("'UPLOADED'"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "btrim(source_key) <> ''",
            name="ck_documents_source_key_not_blank",
        ),
        sa.CheckConstraint(
            "btrim(filename) <> ''",
            name="ck_documents_filename_not_blank",
        ),
        sa.CheckConstraint(
            "btrim(source_type) <> ''",
            name="ck_documents_source_type_not_blank",
        ),
        sa.CheckConstraint(
            "btrim(object_uri) <> ''",
            name="ck_documents_object_uri_not_blank",
        ),
        sa.CheckConstraint(
            "btrim(content_hash) <> ''",
            name="ck_documents_content_hash_not_blank",
        ),
        sa.CheckConstraint(
            "status IN ('UPLOADED', 'INDEXING', 'READY', 'FAILED')",
            name="ck_documents_status",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            [f"{APPLICATION_SCHEMA}.workspaces.id"],
            name="fk_documents_workspace_id_workspaces",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_documents"),
        sa.UniqueConstraint(
            "workspace_id",
            "source_key",
            name="uq_documents_workspace_source_key",
        ),
        schema=APPLICATION_SCHEMA,
    )
    op.create_index(
        "ix_documents_workspace_id",
        "documents",
        ["workspace_id"],
        schema=APPLICATION_SCHEMA,
    )


def downgrade() -> None:
    """Remove the document registry."""
    op.drop_index(
        "ix_documents_workspace_id",
        table_name="documents",
        schema=APPLICATION_SCHEMA,
    )
    op.drop_table("documents", schema=APPLICATION_SCHEMA)
