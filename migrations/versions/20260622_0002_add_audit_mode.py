"""Add explicit audit mode."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260622_0002"
down_revision: str | None = "20260618_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("audit_jobs") as batch_op:
        batch_op.add_column(
            sa.Column(
                "audit_mode",
                sa.String(length=20),
                nullable=False,
                server_default="change",
            )
        )
        batch_op.create_index("ix_audit_jobs_audit_mode", ["audit_mode"])


def downgrade() -> None:
    with op.batch_alter_table("audit_jobs") as batch_op:
        batch_op.drop_index("ix_audit_jobs_audit_mode")
        batch_op.drop_column("audit_mode")

