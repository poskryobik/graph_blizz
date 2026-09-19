"""Defer activation of newly-created document revisions.

Revision ID: 20260918_0008
Revises: 20260918_0007
Create Date: 2026-09-18
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260918_0008"
down_revision: str | None = "20260918_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APPLICATION_SCHEMA = "graph_blizz"


def upgrade() -> None:
    """Allow a new document to remain inactive until indexing succeeds."""
    op.alter_column(
        "documents",
        "active_revision",
        nullable=True,
        server_default=None,
        schema=APPLICATION_SCHEMA,
    )


def downgrade() -> None:
    """Restore eager revision-one activation required by the old schema."""
    op.execute(
        "UPDATE graph_blizz.documents SET active_revision = 1 "
        "WHERE active_revision IS NULL"
    )
    op.alter_column(
        "documents",
        "active_revision",
        nullable=False,
        server_default="1",
        schema=APPLICATION_SCHEMA,
    )
