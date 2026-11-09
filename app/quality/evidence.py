"""Evidence collection for the Quality Intelligence Engine."""

from typing import Any

from app.quality.models import QualityEvidence


class EvidenceCollector:
    """Collect factual evidence from normalized audit reports."""

    def collect(self, report: dict[str, Any]) -> list[QualityEvidence]:
        """Collect report, step, API, review, artifact, and recovery evidence."""
        evidence: list[QualityEvidence] = []
        self._collect_steps(report, evidence)
        self._collect_api(report, evidence)
        self._collect_review(report, evidence)
        self._collect_artifacts(report, evidence)
        self._collect_recovery(report, evidence)
        if report.get("error"):
            evidence.append(
                QualityEvidence(
                    id="report-error",
                    source="report",
                    signal="internal_error",
                    value=report.get("error"),
                    severity="high",
                )
            )
        return evidence

    def _collect_steps(
        self,
        report: dict[str, Any],
        evidence: list[QualityEvidence],
    ) -> None:
        """Collect one evidence item per audit step."""
        for index, step in enumerate(report.get("steps", []) or []):
            name = str(step.get("name") or f"step-{index}")
            status = str(step.get("status") or "unknown")
            evidence.append(
                QualityEvidence(
                    id=f"step-{index}-{name}",
                    source="steps",
                    stage=name,
                    signal=f"step_{status}",
                    value={
                        "status": status,
                        "exit_code": step.get("exit_code"),
                        "duration_ms": step.get("duration_ms"),
                    },
                    severity="high" if status in {"failed", "timeout"} else None,
                )
            )

    def _collect_api(
        self,
        report: dict[str, Any],
        evidence: list[QualityEvidence],
    ) -> None:
        """Collect API result and warning evidence."""
        api = report.get("api_validation", {}) or {}
        for index, result in enumerate(api.get("results", []) or []):
            if result.get("passed"):
                continue
            evidence.append(
                QualityEvidence(
                    id=f"api-test-failed-{index}",
                    source="api_validation",
                    stage="api_acceptance",
                    signal="api_test_failed",
                    value=result.get("failures", []),
                    severity="medium",
                    scope={
                        "test_name": result.get("name"),
                        "status_code": result.get("status_code"),
                    },
                )
            )
        for index, warning in enumerate(api.get("warnings", []) or []):
            evidence.append(
                QualityEvidence(
                    id=f"api-warning-{index}",
                    source="api_validation",
                    stage="api_generation",
                    signal="api_warning",
                    value=warning,
                    severity="low",
                )
            )

    def _collect_review(
        self,
        report: dict[str, Any],
        evidence: list[QualityEvidence],
    ) -> None:
        """Collect Code Review findings as evidence."""
        review = report.get("code_review", {}) or {}
        for index, finding in enumerate(review.get("findings", []) or []):
            severity = str(finding.get("severity") or "low")
            evidence.append(
                QualityEvidence(
                    id=f"review-finding-{index}",
                    source="code_review",
                    stage="code_quality",
                    signal="review_finding",
                    value=finding.get("message"),
                    severity=severity,
                    scope={
                        "file": finding.get("file"),
                        "line": finding.get("line"),
                        "rule_id": finding.get("rule_id"),
                    },
                )
            )

    def _collect_artifacts(
        self,
        report: dict[str, Any],
        evidence: list[QualityEvidence],
    ) -> None:
        """Collect artifact availability as evidence."""
        for artifact_type, path in (report.get("artifacts", {}) or {}).items():
            evidence.append(
                QualityEvidence(
                    id=f"artifact-{artifact_type}",
                    source="artifacts",
                    signal="artifact_available",
                    artifact_type=artifact_type,
                    artifact_path=str(path),
                )
            )

    def _collect_recovery(
        self,
        report: dict[str, Any],
        evidence: list[QualityEvidence],
    ) -> None:
        """Collect self-healing attempts as evidence."""
        recovery = report.get("recovery", {}) or {}
        for index, attempt in enumerate(recovery.get("attempts", []) or []):
            evidence.append(
                QualityEvidence(
                    id=f"recovery-attempt-{index}",
                    source="recovery",
                    stage="recovery",
                    signal="recovery_attempt",
                    value=attempt,
                )
            )
