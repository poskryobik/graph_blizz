"""Allow the durable document replacement lifecycle.

Revision ID: 20260918_0006
Revises: 20260918_0005
Create Date: 2026-09-18
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260918_0006"
down_revision: str | None = "20260918_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APPLICATION_SCHEMA = "graph_blizz"


def upgrade() -> None:
    """Permit documents to expose an in-progress immutable replacement."""
    op.drop_constraint(
        "ck_documents_status", "documents", schema=APPLICATION_SCHEMA, type_="check"
    )
    op.create_check_constraint(
        "ck_documents_status",
        "documents",
        "status IN ('UPLOADED', 'INDEXING', 'READY', 'UPDATING', 'FAILED')",
        schema=APPLICATION_SCHEMA,
    )


def downgrade() -> None:
    """Restore the pre-replacement status set."""
    op.execute(
        "UPDATE graph_blizz.documents SET status = 'READY' WHERE status = 'UPDATING'"
    )
    op.drop_constraint(
        "ck_documents_status", "documents", schema=APPLICATION_SCHEMA, type_="check"
    )
    op.create_check_constraint(
        "ck_documents_status",
        "documents",
        "status IN ('UPLOADED', 'INDEXING', 'READY', 'FAILED')",
        schema=APPLICATION_SCHEMA,
    )
