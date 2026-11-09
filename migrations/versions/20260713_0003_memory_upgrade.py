"""Add structured project and cross-repository recovery memory."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260713_0003"
down_revision: str | None = "20260622_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "project_recovery_memories",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("memory_id", sa.String(length=36), nullable=False),
        sa.Column("scope", sa.String(length=30), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("trust_level", sa.String(length=20), nullable=False),
        sa.Column("repo_hash", sa.String(length=64), nullable=False),
        sa.Column("signature_key", sa.String(length=64), nullable=False),
        sa.Column("match_key", sa.String(length=255), nullable=False),
        sa.Column("stage", sa.String(length=100), nullable=False),
        sa.Column("category", sa.String(length=100), nullable=False),
        sa.Column("pattern_key", sa.String(length=100), nullable=False),
        sa.Column("profile", sa.String(length=50), nullable=True),
        sa.Column("runtime", sa.String(length=50), nullable=True),
        sa.Column("framework", sa.String(length=100), nullable=True),
        sa.Column("services_json", sa.JSON(), nullable=False),
        sa.Column("action", sa.String(length=150), nullable=False),
        sa.Column("success_count", sa.Integer(), nullable=False),
        sa.Column("avg_duration_ms", sa.Integer(), nullable=True),
        sa.Column("signature_json", sa.JSON(), nullable=False),
        sa.Column("content_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("memory_id"),
        sa.UniqueConstraint(
            "repo_hash",
            "signature_key",
            "action",
            name="uq_project_recovery_repo_signature_action",
        ),
    )
    op.create_index(
        "ix_project_recovery_repo_signature",
        "project_recovery_memories",
        ["repo_hash", "signature_key"],
    )
    op.create_index(
        "ix_project_recovery_match_key",
        "project_recovery_memories",
        ["match_key"],
    )

    op.create_table(
        "cross_recovery_candidates",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("match_key", sa.String(length=255), nullable=False),
        sa.Column("action", sa.String(length=150), nullable=False),
        sa.Column("kind", sa.String(length=50), nullable=False),
        sa.Column("stage", sa.String(length=100), nullable=False),
        sa.Column("category", sa.String(length=100), nullable=False),
        sa.Column("pattern_key", sa.String(length=100), nullable=False),
        sa.Column("repo_count", sa.Integer(), nullable=False),
        sa.Column("success_count", sa.Integer(), nullable=False),
        sa.Column("repo_hashes", sa.JSON(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "match_key",
            "action",
            name="uq_cross_recovery_candidate_match_action",
        ),
    )
    op.create_index(
        "ix_cross_recovery_candidates_match_key",
        "cross_recovery_candidates",
        ["match_key"],
    )

    op.create_table(
        "cross_recovery_experience",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("memory_id", sa.String(length=36), nullable=False),
        sa.Column("scope", sa.String(length=30), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("trust_level", sa.String(length=20), nullable=False),
        sa.Column("match_key", sa.String(length=255), nullable=False),
        sa.Column("action", sa.String(length=150), nullable=False),
        sa.Column("kind", sa.String(length=50), nullable=False),
        sa.Column("stage", sa.String(length=100), nullable=False),
        sa.Column("category", sa.String(length=100), nullable=False),
        sa.Column("pattern_key", sa.String(length=100), nullable=False),
        sa.Column("repo_count", sa.Integer(), nullable=False),
        sa.Column("repo_hashes", sa.JSON(), nullable=False),
        sa.Column("success_count", sa.Integer(), nullable=False),
        sa.Column("content_json", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("memory_id"),
        sa.UniqueConstraint(
            "match_key",
            "action",
            name="uq_cross_recovery_experience_match_action",
        ),
    )
    op.create_index(
        "ix_cross_recovery_experience_match_key",
        "cross_recovery_experience",
        ["match_key"],
    )
    op.create_index(
        "ix_cross_recovery_experience_kind_stage",
        "cross_recovery_experience",
        ["kind", "stage"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_cross_recovery_experience_kind_stage",
        table_name="cross_recovery_experience",
    )
    op.drop_index(
        "ix_cross_recovery_experience_match_key",
        table_name="cross_recovery_experience",
    )
    op.drop_table("cross_recovery_experience")
    op.drop_index(
        "ix_cross_recovery_candidates_match_key",
        table_name="cross_recovery_candidates",
    )
    op.drop_table("cross_recovery_candidates")
    op.drop_index(
        "ix_project_recovery_match_key",
        table_name="project_recovery_memories",
    )
    op.drop_index(
        "ix_project_recovery_repo_signature",
        table_name="project_recovery_memories",
    )
    op.drop_table("project_recovery_memories")
