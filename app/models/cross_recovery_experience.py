"""Runtime-consumable cross-repository recovery experience model."""

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CrossRecoveryExperience(Base):
    """A promoted recovery action with evidence from multiple repositories."""

    __tablename__ = "cross_recovery_experience"
    __table_args__ = (
        UniqueConstraint(
            "match_key",
            "action",
            name="uq_cross_recovery_experience_match_action",
        ),
        Index("ix_cross_recovery_experience_match_key", "match_key"),
        Index("ix_cross_recovery_experience_kind_stage", "kind", "stage"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    memory_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    scope: Mapped[str] = mapped_column(String(30), nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    trust_level: Mapped[str] = mapped_column(String(20), nullable=False)

    match_key: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(150), nullable=False)
    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    stage: Mapped[str] = mapped_column(String(100), nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    pattern_key: Mapped[str] = mapped_column(String(100), nullable=False)
    repo_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    repo_hashes: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    success_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    content_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
