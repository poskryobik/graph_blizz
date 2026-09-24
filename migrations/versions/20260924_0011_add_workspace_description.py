"""Add optional workspace descriptions.

Revision ID: 20260924_0011
Revises: 20260921_0010
Create Date: 2026-09-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260924_0011"
down_revision: str | None = "20260921_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APPLICATION_SCHEMA = "graph_blizz"


def upgrade() -> None:
    """Add a nullable description without rewriting existing rows."""
    op.add_column(
        "workspaces",
        sa.Column("description", sa.Text(), nullable=True),
        schema=APPLICATION_SCHEMA,
    )


def downgrade() -> None:
    """Remove the workspace description column."""
    op.drop_column("workspaces", "description", schema=APPLICATION_SCHEMA)
