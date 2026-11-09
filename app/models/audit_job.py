from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class AuditJob(Base):
    __tablename__ = "audit_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    requester_id: Mapped[str] = mapped_column(String(255), index=True)
    repo: Mapped[str] = mapped_column(Text)
    audit_mode: Mapped[str] = mapped_column(String(20), index=True)
    base_commit: Mapped[str | None] = mapped_column(String(255), nullable=True)
    patch_hash: Mapped[str | None] = mapped_column(String(71), nullable=True)
    api_document_hash: Mapped[str] = mapped_column(String(71))
    api_schema_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    test_suite_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    test_suite_hash: Mapped[str | None] = mapped_column(String(71), nullable=True)
    startup_command: Mapped[str] = mapped_column(Text)
    service_port: Mapped[int] = mapped_column(Integer)
    profile: Mapped[str] = mapped_column(String(50))
    sandbox_backend: Mapped[str] = mapped_column(String(50), default="docker")
    sandbox_image: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(30), index=True)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    risk_level: Mapped[str] = mapped_column(String(20), default="unknown")
    workspace_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    steps = relationship(
        "AuditStep",
        back_populates="job",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    artifacts = relationship(
        "AuditArtifact",
        back_populates="job",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
