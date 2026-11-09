"""Cross-repository recovery candidate aggregation model."""

from datetime import datetime

from sqlalchemy import JSON, DateTime, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CrossRecoveryCandidate(Base):
    """Intermediate statistics; candidates never drive runtime recovery."""

    __tablename__ = "cross_recovery_candidates"
    __table_args__ = (
        UniqueConstraint(
            "match_key",
            "action",
            name="uq_cross_recovery_candidate_match_action",
        ),
        Index("ix_cross_recovery_candidates_match_key", "match_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    match_key: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(150), nullable=False)
    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    stage: Mapped[str] = mapped_column(String(100), nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    pattern_key: Mapped[str] = mapped_column(String(100), nullable=False)
    repo_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    success_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    repo_hashes: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
