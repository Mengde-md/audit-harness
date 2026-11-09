"""Data models used by the Quality Intelligence Engine."""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class QualityEvidence:
    """A factual signal collected from an audit report."""

    id: str
    source: str
    signal: str
    stage: str | None = None
    value: Any | None = None
    severity: str | None = None
    artifact_type: str | None = None
    artifact_path: str | None = None
    scope: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return {
            "id": self.id,
            "source": self.source,
            "stage": self.stage,
            "signal": self.signal,
            "value": self.value,
            "severity": self.severity,
            "artifact_type": self.artifact_type,
            "artifact_path": self.artifact_path,
            "scope": self.scope,
        }


@dataclass(frozen=True)
class Penalty:
    """A score deduction with traceable evidence."""

    rule_id: str
    domain: str
    points: int
    reason: str
    evidence_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return {
            "rule_id": self.rule_id,
            "domain": self.domain,
            "points": self.points,
            "reason": self.reason,
            "evidence_ids": self.evidence_ids,
        }


@dataclass(frozen=True)
class DomainScore:
    """Independent score for one quality domain."""

    name: str
    max_score: int
    score: int
    grade: str
    status: str
    needs_optimization: bool
    summary: str
    penalties: list[Penalty] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return {
            "max_score": self.max_score,
            "score": self.score,
            "grade": self.grade,
            "status": self.status,
            "needs_optimization": self.needs_optimization,
            "summary": self.summary,
            "penalties": [item.to_dict() for item in self.penalties],
            "suggestions": self.suggestions,
        }
