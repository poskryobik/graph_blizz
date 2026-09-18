"""Add immutable document revisions and migrate Demo sources.

Revision ID: 20260918_0004
Revises: 20260917_0003
Create Date: 2026-09-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260918_0004"
down_revision: str | None = "20260917_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APPLICATION_SCHEMA = "graph_blizz"


def upgrade() -> None:
    """Split immutable source metadata into revision rows without data loss."""
    op.add_column(
        "documents",
        sa.Column("active_revision", sa.Integer(), server_default="1", nullable=False),
        schema=APPLICATION_SCHEMA,
    )
    op.create_check_constraint(
        "ck_documents_active_revision_positive",
        "documents",
        "active_revision > 0",
        schema=APPLICATION_SCHEMA,
    )
    op.create_table(
        "document_revisions",
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("object_uri", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "revision > 0", name="ck_document_revisions_revision_positive"
        ),
        sa.CheckConstraint(
            "btrim(object_uri) <> ''",
            name="ck_document_revisions_object_uri_not_blank",
        ),
        sa.CheckConstraint(
            "btrim(content_hash) <> ''",
            name="ck_document_revisions_content_hash_not_blank",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            [f"{APPLICATION_SCHEMA}.documents.id"],
            name="fk_document_revisions_document_id_documents",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "document_id", "revision", name="pk_document_revisions"
        ),
        sa.UniqueConstraint("object_uri", name="uq_document_revisions_object_uri"),
        schema=APPLICATION_SCHEMA,
    )
    op.execute(
        """
        INSERT INTO graph_blizz.document_revisions (
            document_id, revision, object_uri, content_hash, created_at
        )
        SELECT id, 1, object_uri, content_hash, created_at
        FROM graph_blizz.documents
        """
    )
    op.create_foreign_key(
        "fk_documents_active_revision_document_revisions",
        "documents",
        "document_revisions",
        ["id", "active_revision"],
        ["document_id", "revision"],
        source_schema=APPLICATION_SCHEMA,
        referent_schema=APPLICATION_SCHEMA,
        deferrable=True,
        initially="DEFERRED",
    )
    op.drop_constraint(
        "ck_documents_object_uri_not_blank",
        "documents",
        schema=APPLICATION_SCHEMA,
        type_="check",
    )
    op.drop_constraint(
        "ck_documents_content_hash_not_blank",
        "documents",
        schema=APPLICATION_SCHEMA,
        type_="check",
    )
    op.drop_column("documents", "object_uri", schema=APPLICATION_SCHEMA)
    op.drop_column("documents", "content_hash", schema=APPLICATION_SCHEMA)
    op.execute(
        """
        CREATE FUNCTION graph_blizz.reject_document_revision_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'document revisions are immutable';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_document_revisions_immutable
        BEFORE UPDATE ON graph_blizz.document_revisions
        FOR EACH ROW EXECUTE FUNCTION graph_blizz.reject_document_revision_mutation()
        """
    )


def downgrade() -> None:
    """Collapse active revision metadata back into the Demo document table."""
    op.add_column(
        "documents",
        sa.Column("object_uri", sa.Text(), nullable=True),
        schema=APPLICATION_SCHEMA,
    )
    op.add_column(
        "documents",
        sa.Column("content_hash", sa.Text(), nullable=True),
        schema=APPLICATION_SCHEMA,
    )
    op.execute(
        """
        UPDATE graph_blizz.documents AS d
        SET object_uri = r.object_uri, content_hash = r.content_hash
        FROM graph_blizz.document_revisions AS r
        WHERE r.document_id = d.id AND r.revision = d.active_revision
        """
    )
    op.alter_column(
        "documents", "object_uri", nullable=False, schema=APPLICATION_SCHEMA
    )
    op.alter_column(
        "documents", "content_hash", nullable=False, schema=APPLICATION_SCHEMA
    )
    op.create_check_constraint(
        "ck_documents_object_uri_not_blank",
        "documents",
        "btrim(object_uri) <> ''",
        schema=APPLICATION_SCHEMA,
    )
    op.create_check_constraint(
        "ck_documents_content_hash_not_blank",
        "documents",
        "btrim(content_hash) <> ''",
        schema=APPLICATION_SCHEMA,
    )
    op.execute(
        "DROP TRIGGER trg_document_revisions_immutable "
        "ON graph_blizz.document_revisions"
    )
    op.execute("DROP FUNCTION graph_blizz.reject_document_revision_mutation()")
    op.drop_constraint(
        "fk_documents_active_revision_document_revisions",
        "documents",
        schema=APPLICATION_SCHEMA,
        type_="foreignkey",
    )
    op.drop_table("document_revisions", schema=APPLICATION_SCHEMA)
    op.drop_constraint(
        "ck_documents_active_revision_positive",
        "documents",
        schema=APPLICATION_SCHEMA,
        type_="check",
    )
    op.drop_column("documents", "active_revision", schema=APPLICATION_SCHEMA)
