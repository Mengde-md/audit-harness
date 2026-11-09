"""Shared models for code review results."""

from typing import Any, Literal

from pydantic import BaseModel, Field


Severity = Literal["high", "medium", "low"]
ReviewSource = Literal["deterministic", "llm"]


class ReviewFinding(BaseModel):
    """One actionable code review finding."""

    source: ReviewSource
    rule_id: str
    severity: Severity
    category: str
    file: str
    line: int = 1
    message: str
    suggestion: str
    evidence: str | None = None


class LlmReviewMeta(BaseModel):
    """Execution metadata for patch-level LLM review."""

    enabled: bool = False
    skipped_reason: str | None = None
    model: str | None = None
    warning: str | None = None


class CodeReviewResult(BaseModel):
    """Complete review payload stored in audit summary and artifacts."""

    audit_mode: Literal["change", "full"]
    scanned_files: list[str] = Field(default_factory=list)
    deterministic_rule_count: int = 0
    llm_review: LlmReviewMeta = Field(default_factory=LlmReviewMeta)
    findings: list[ReviewFinding] = Field(default_factory=list)
    test_suggestions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        """Return severity counters for scoring and summary display."""
        return {
            "total": len(self.findings),
            "high": sum(item.severity == "high" for item in self.findings),
            "medium": sum(item.severity == "medium" for item in self.findings),
            "low": sum(item.severity == "low" for item in self.findings),
        }

    def to_summary(self) -> dict[str, Any]:
        """Return a compact but complete JSON-serializable summary."""
        payload = self.model_dump(mode="json")
        payload["counts"] = self.counts
        return payload

