"""Add durable workspace reindex jobs.

Revision ID: 20260921_0010
Revises: 20260921_0009
Create Date: 2026-09-21
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260921_0010"
down_revision: str | None = "20260921_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APPLICATION_SCHEMA = "graph_blizz"


def upgrade() -> None:
    """Permit reindex jobs and successful revision version reconciliation."""
    op.drop_constraint(
        "ck_jobs_job_type", "jobs", schema=APPLICATION_SCHEMA, type_="check"
    )
    op.create_check_constraint(
        "ck_jobs_job_type",
        "jobs",
        "job_type IN ('INDEX_DOCUMENT', 'DELETE_DOCUMENT', 'REINDEX_DOCUMENT')",
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
               OR ((NEW.parser_version IS DISTINCT FROM OLD.parser_version
                    OR NEW.chunk_schema_version IS DISTINCT FROM OLD.chunk_schema_version
                    OR NEW.index_schema_version IS DISTINCT FROM OLD.index_schema_version)
                   AND NOT (OLD.requires_reindex AND NOT NEW.requires_reindex))
            THEN
                RAISE EXCEPTION 'document revisions are immutable';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )


def downgrade() -> None:
    """Remove reindex jobs after cancelling their persisted work."""
    op.execute(
        "UPDATE graph_blizz.jobs SET status = 'CANCELLED', finished_at = now(), "
        "lease_owner = NULL, lease_expires_at = NULL, heartbeat_at = NULL, "
        "updated_at = now() WHERE job_type = 'REINDEX_DOCUMENT' "
        "AND status IN ('PENDING', 'RUNNING', 'RETRY')"
    )
    op.execute("DELETE FROM graph_blizz.jobs WHERE job_type = 'REINDEX_DOCUMENT'")
    op.drop_constraint(
        "ck_jobs_job_type", "jobs", schema=APPLICATION_SCHEMA, type_="check"
    )
    op.create_check_constraint(
        "ck_jobs_job_type",
        "jobs",
        "job_type IN ('INDEX_DOCUMENT', 'DELETE_DOCUMENT')",
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
