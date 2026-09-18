"""Create durable indexing jobs.

Revision ID: 20260918_0005
Revises: 20260918_0004
Create Date: 2026-09-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260918_0005"
down_revision: str | None = "20260918_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APPLICATION_SCHEMA = "graph_blizz"


def upgrade() -> None:
    """Create persistent jobs and enforce lifecycle invariants in PostgreSQL."""
    op.create_table(
        "jobs",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("document_revision", sa.Integer(), nullable=False),
        sa.Column(
            "job_type",
            sa.Text(),
            server_default=sa.text("'INDEX_DOCUMENT'"),
            nullable=False,
        ),
        sa.Column(
            "status", sa.Text(), server_default=sa.text("'PENDING'"), nullable=False
        ),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="3", nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("lease_owner", sa.String(128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_detail", sa.String(500), nullable=True),
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
            "document_revision > 0", name="ck_jobs_document_revision_positive"
        ),
        sa.CheckConstraint("job_type = 'INDEX_DOCUMENT'", name="ck_jobs_job_type"),
        sa.CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'RETRY', 'SUCCEEDED', 'FAILED', 'CANCELLED')",
            name="ck_jobs_status",
        ),
        sa.CheckConstraint(
            "max_attempts > 0 AND attempts BETWEEN 0 AND max_attempts",
            name="ck_jobs_attempts",
        ),
        sa.CheckConstraint(
            "updated_at >= created_at AND available_at >= created_at",
            name="ck_jobs_timestamp_order",
        ),
        sa.CheckConstraint(
            "status <> 'PENDING' OR attempts = 0", name="ck_jobs_pending_attempts"
        ),
        sa.CheckConstraint(
            "status NOT IN ('RUNNING', 'RETRY', 'SUCCEEDED', 'FAILED') OR (attempts > 0 AND started_at IS NOT NULL)",
            name="ck_jobs_started",
        ),
        sa.CheckConstraint(
            "started_at IS NULL OR started_at >= created_at",
            name="ck_jobs_started_order",
        ),
        sa.CheckConstraint(
            "(attempts = 0) = (started_at IS NULL)",
            name="ck_jobs_attempts_started_pair",
        ),
        sa.CheckConstraint(
            "(status = 'RUNNING' AND attempts > 0 AND lease_owner IS NOT NULL AND btrim(lease_owner) <> '' AND lease_expires_at IS NOT NULL AND heartbeat_at IS NOT NULL AND started_at IS NOT NULL AND lease_expires_at > heartbeat_at) OR (status <> 'RUNNING' AND lease_owner IS NULL AND lease_expires_at IS NULL AND heartbeat_at IS NULL)",
            name="ck_jobs_lease",
        ),
        sa.CheckConstraint(
            "heartbeat_at IS NULL OR heartbeat_at >= started_at",
            name="ck_jobs_heartbeat_order",
        ),
        sa.CheckConstraint(
            "updated_at >= COALESCE(started_at, created_at) AND updated_at >= COALESCE(heartbeat_at, created_at)",
            name="ck_jobs_updated_order",
        ),
        sa.CheckConstraint(
            "(status IN ('SUCCEEDED', 'FAILED', 'CANCELLED')) = (finished_at IS NOT NULL)",
            name="ck_jobs_finished_at",
        ),
        sa.CheckConstraint(
            "finished_at IS NULL OR finished_at >= COALESCE(started_at, created_at)",
            name="ck_jobs_finished_order",
        ),
        sa.CheckConstraint(
            "finished_at IS NULL OR updated_at >= finished_at",
            name="ck_jobs_updated_after_finished",
        ),
        sa.CheckConstraint(
            "status <> 'RETRY' OR attempts < max_attempts",
            name="ck_jobs_retry_attempts",
        ),
        sa.CheckConstraint(
            "error_code IS NULL OR (status IN ('RETRY', 'FAILED') AND error_code IN ('INDEXING_FAILED', 'DEPENDENCY_UNAVAILABLE', 'ATTEMPTS_EXHAUSTED', 'INTERNAL_ERROR'))",
            name="ck_jobs_error_code",
        ),
        sa.CheckConstraint(
            "error_detail IS NULL",
            name="ck_jobs_error_detail",
        ),
        sa.ForeignKeyConstraint(
            ["document_id", "document_revision"],
            [
                f"{APPLICATION_SCHEMA}.document_revisions.document_id",
                f"{APPLICATION_SCHEMA}.document_revisions.revision",
            ],
            name="fk_jobs_document_revision",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_jobs"),
        schema=APPLICATION_SCHEMA,
    )
    op.create_index(
        "ix_jobs_available",
        "jobs",
        ["status", "available_at", "created_at"],
        schema=APPLICATION_SCHEMA,
    )
    op.create_index(
        "uq_jobs_active_document_mutation",
        "jobs",
        ["document_id"],
        unique=True,
        schema=APPLICATION_SCHEMA,
        postgresql_where=sa.text("status IN ('PENDING', 'RUNNING', 'RETRY')"),
    )


def downgrade() -> None:
    """Remove durable indexing jobs."""
    op.drop_index(
        "uq_jobs_active_document_mutation", table_name="jobs", schema=APPLICATION_SCHEMA
    )
    op.drop_index("ix_jobs_available", table_name="jobs", schema=APPLICATION_SCHEMA)
    op.drop_table("jobs", schema=APPLICATION_SCHEMA)
