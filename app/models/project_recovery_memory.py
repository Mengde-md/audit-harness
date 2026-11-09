"""Project-scoped successful recovery memory model."""

from datetime import datetime

from sqlalchemy import JSON, DateTime, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ProjectRecoveryMemory(Base):
    """A successful action for one exact failure signature in one repository."""

    __tablename__ = "project_recovery_memories"
    __table_args__ = (
        UniqueConstraint(
            "repo_hash",
            "signature_key",
            "action",
            name="uq_project_recovery_repo_signature_action",
        ),
        Index(
            "ix_project_recovery_repo_signature",
            "repo_hash",
            "signature_key",
        ),
        Index("ix_project_recovery_match_key", "match_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    memory_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    scope: Mapped[str] = mapped_column(String(30), nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    trust_level: Mapped[str] = mapped_column(String(20), nullable=False)

    repo_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    signature_key: Mapped[str] = mapped_column(String(64), nullable=False)
    match_key: Mapped[str] = mapped_column(String(255), nullable=False)
    stage: Mapped[str] = mapped_column(String(100), nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    pattern_key: Mapped[str] = mapped_column(String(100), nullable=False)
    profile: Mapped[str | None] = mapped_column(String(50), nullable=True)
    runtime: Mapped[str | None] = mapped_column(String(50), nullable=True)
    framework: Mapped[str | None] = mapped_column(String(100), nullable=True)
    services_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)

    action: Mapped[str] = mapped_column(String(150), nullable=False)
    success_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    avg_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    signature_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    content_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
