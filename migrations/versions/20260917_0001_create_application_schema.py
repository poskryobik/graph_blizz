"""Create the dedicated application schema.

Revision ID: 20260917_0001
Revises:
Create Date: 2026-09-17
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260917_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APPLICATION_SCHEMA = "graph_blizz"


def upgrade() -> None:
    """Create the namespace for application-owned database objects."""
    op.execute(f'CREATE SCHEMA "{APPLICATION_SCHEMA}"')


def downgrade() -> None:
    """Drop the empty application namespace."""
    op.execute(f'DROP SCHEMA "{APPLICATION_SCHEMA}"')
