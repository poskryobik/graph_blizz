"""Create persistent workspaces.

Revision ID: 20260917_0002
Revises: 20260917_0001
Create Date: 2026-09-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260917_0002"
down_revision: str | None = "20260917_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APPLICATION_SCHEMA = "graph_blizz"


def upgrade() -> None:
    """Create the minimal workspace metadata table and storage-key guard."""
    op.create_table(
        "workspaces",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column(
            "storage_key",
            sa.Text(),
            server_default=sa.text(
                "'ws_' || replace(gen_random_uuid()::text, '-', '')"
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Text(),
            server_default=sa.text("'ACTIVE'"),
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
        sa.CheckConstraint("btrim(name) <> ''", name="ck_workspaces_name_not_blank"),
        sa.CheckConstraint("btrim(slug) <> ''", name="ck_workspaces_slug_not_blank"),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'ARCHIVED')",
            name="ck_workspaces_status",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_workspaces"),
        sa.UniqueConstraint("slug", name="uq_workspaces_slug"),
        sa.UniqueConstraint("storage_key", name="uq_workspaces_storage_key"),
        schema=APPLICATION_SCHEMA,
    )
    op.execute(
        """
        CREATE FUNCTION graph_blizz.reject_workspace_storage_key_change()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.storage_key IS DISTINCT FROM OLD.storage_key THEN
                RAISE EXCEPTION 'workspace storage_key is immutable';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER workspaces_storage_key_immutable
        BEFORE UPDATE OF storage_key ON graph_blizz.workspaces
        FOR EACH ROW
        EXECUTE FUNCTION graph_blizz.reject_workspace_storage_key_change()
        """
    )


def downgrade() -> None:
    """Remove workspace metadata and its storage-key guard."""
    op.drop_table("workspaces", schema=APPLICATION_SCHEMA)
    op.execute("DROP FUNCTION graph_blizz.reject_workspace_storage_key_change()")
