"""Allow durable document deletion.

Revision ID: 20260918_0007
Revises: 20260918_0006
Create Date: 2026-09-18
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260918_0007"
down_revision: str | None = "20260918_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APPLICATION_SCHEMA = "graph_blizz"


def upgrade() -> None:
    """Permit deletion jobs and document deletion lifecycle states."""
    op.drop_constraint(
        "ck_jobs_job_type", "jobs", schema=APPLICATION_SCHEMA, type_="check"
    )
    op.create_check_constraint(
        "ck_jobs_job_type",
        "jobs",
        "job_type IN ('INDEX_DOCUMENT', 'DELETE_DOCUMENT')",
        schema=APPLICATION_SCHEMA,
    )
    op.drop_constraint(
        "ck_documents_status", "documents", schema=APPLICATION_SCHEMA, type_="check"
    )
    op.create_check_constraint(
        "ck_documents_status",
        "documents",
        "status IN ('UPLOADED', 'INDEXING', 'READY', 'UPDATING', 'DELETING', 'DELETED', 'FAILED')",
        schema=APPLICATION_SCHEMA,
    )


def downgrade() -> None:
    """Restore the pre-deletion job and document status sets."""
    op.execute(
        "UPDATE graph_blizz.jobs SET status = 'CANCELLED', finished_at = now(), "
        "updated_at = now() WHERE job_type = 'DELETE_DOCUMENT' "
        "AND status IN ('PENDING', 'RUNNING', 'RETRY')"
    )
    op.execute("DELETE FROM graph_blizz.jobs WHERE job_type = 'DELETE_DOCUMENT'")
    op.execute(
        "UPDATE graph_blizz.documents SET status = 'READY' "
        "WHERE status IN ('DELETING', 'DELETED')"
    )
    op.drop_constraint(
        "ck_documents_status", "documents", schema=APPLICATION_SCHEMA, type_="check"
    )
    op.create_check_constraint(
        "ck_documents_status",
        "documents",
        "status IN ('UPLOADED', 'INDEXING', 'READY', 'UPDATING', 'FAILED')",
        schema=APPLICATION_SCHEMA,
    )
    op.drop_constraint(
        "ck_jobs_job_type", "jobs", schema=APPLICATION_SCHEMA, type_="check"
    )
    op.create_check_constraint(
        "ck_jobs_job_type",
        "jobs",
        "job_type = 'INDEX_DOCUMENT'",
        schema=APPLICATION_SCHEMA,
    )
