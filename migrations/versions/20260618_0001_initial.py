"""Create audit job, step, and artifact tables."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260618_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("requester_id", sa.String(length=255), nullable=False),
        sa.Column("repo", sa.Text(), nullable=False),
        sa.Column("base_commit", sa.String(length=255), nullable=True),
        sa.Column("patch_hash", sa.String(length=71), nullable=True),
        sa.Column("api_document_hash", sa.String(length=71), nullable=False),
        sa.Column("api_schema_id", sa.String(length=36), nullable=True),
        sa.Column("test_suite_id", sa.String(length=36), nullable=True),
        sa.Column("test_suite_hash", sa.String(length=71), nullable=True),
        sa.Column("startup_command", sa.Text(), nullable=False),
        sa.Column("service_port", sa.Integer(), nullable=False),
        sa.Column("profile", sa.String(length=50), nullable=False),
        sa.Column("sandbox_backend", sa.String(length=50), nullable=False),
        sa.Column("sandbox_image", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("risk_level", sa.String(length=20), nullable=False),
        sa.Column("workspace_path", sa.Text(), nullable=True),
        sa.Column("error_type", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_jobs_requester_id", "audit_jobs", ["requester_id"])
    op.create_index("ix_audit_jobs_status", "audit_jobs", ["status"])

    op.create_table(
        "audit_artifacts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("audit_id", sa.String(length=36), nullable=False),
        sa.Column("artifact_type", sa.String(length=100), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["audit_id"], ["audit_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_audit_artifacts_audit_id",
        "audit_artifacts",
        ["audit_id"],
    )

    op.create_table(
        "audit_steps",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("audit_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("command", sa.Text(), nullable=False),
        sa.Column("image", sa.String(length=255), nullable=False),
        sa.Column("container_id", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("stdout_tail", sa.Text(), nullable=False),
        sa.Column("stderr_tail", sa.Text(), nullable=False),
        sa.Column("findings", sa.JSON(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["audit_id"], ["audit_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_steps_audit_id", "audit_steps", ["audit_id"])


def downgrade() -> None:
    op.drop_index("ix_audit_steps_audit_id", table_name="audit_steps")
    op.drop_table("audit_steps")
    op.drop_index("ix_audit_artifacts_audit_id", table_name="audit_artifacts")
    op.drop_table("audit_artifacts")
    op.drop_index("ix_audit_jobs_status", table_name="audit_jobs")
    op.drop_index("ix_audit_jobs_requester_id", table_name="audit_jobs")
    op.drop_table("audit_jobs")

