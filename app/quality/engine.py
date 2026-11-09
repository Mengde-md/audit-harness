"""Orchestration entrypoint for the Quality Intelligence Engine."""

from typing import Any

from app.quality.confidence import ConfidenceEvaluator
from app.quality.evidence import EvidenceCollector
from app.quality.gate import GateEvaluator
from app.quality.metrics import MetricExtractor
from app.quality.scoring import ScoringEngine


class QualityEngine:
    """Build domain scores, confidence, gate, metrics, and attribution."""

    def __init__(self) -> None:
        """Create engine components."""
        self.evidence_collector = EvidenceCollector()
        self.metric_extractor = MetricExtractor()
        self.scoring_engine = ScoringEngine()
        self.confidence_evaluator = ConfidenceEvaluator()
        self.gate_evaluator = GateEvaluator()

    def build(self, report: dict[str, Any]) -> dict[str, Any]:
        """Build the full quality intelligence payload."""
        attribution = self._classify(report)
        evidence = self.evidence_collector.collect(report)
        metrics = self.metric_extractor.extract(report, attribution)
        domains = self.scoring_engine.score(report, metrics)
        confidence = self.confidence_evaluator.evaluate(report, metrics)
        gate = self.gate_evaluator.evaluate(metrics, domains, confidence)
        legacy = self._legacy(report, metrics, attribution)
        return {
            "domains": domains,
            "confidence": confidence,
            "gate": gate,
            "metrics": metrics,
            "evidence": [item.to_dict() for item in evidence],
            "failure_attribution": attribution,
            "legacy": legacy,
            # Compatibility fields used by existing APIs, docs, and tests.
            "score": legacy["score"],
            "grade": legacy["grade"],
            "dimensions": legacy["dimensions"],
            "step_count": len(report.get("steps", []) or []),
        }

    def _legacy(
        self,
        report: dict[str, Any],
        metrics: dict[str, Any],
        attribution: dict[str, Any],
    ) -> dict[str, Any]:
        """Keep the previous single-score contract for existing consumers."""
        api_total = metrics["api_total"]
        api_passed = metrics["api_passed"]
        dependency_score = 0 if attribution["category"] == "DEPENDENCY_ERROR" else 15
        check_score = 15 if metrics["check_total"] and metrics["check_pass_rate"] == 1 else 0
        startup_score = 15 if metrics["setup_succeeded"] and metrics["service_started"] else 0
        api_score = round((api_passed / api_total) * 25) if api_total else 0
        code_review_score = max(
            0,
            20
            - metrics["review_high_count"] * 10
            - metrics["review_medium_count"] * 4
            - metrics["review_low_count"],
        )
        observability_score = 0
        if metrics["has_report_json"]:
            observability_score += 3
        if metrics["has_execution_events"]:
            observability_score += 3
        if metrics["has_acceptance_result"]:
            observability_score += 2
        if metrics["llm_mock_call_count"] > 0:
            observability_score += 2
        dimensions = {
            "dependency": dependency_score,
            "checks": check_score,
            "startup": startup_score,
            "api": api_score,
            "code_review": code_review_score,
            "observability": observability_score,
        }
        score = sum(dimensions.values())
        return {
            "score": score,
            "grade": self._grade(score),
            "dimensions": dimensions,
        }

    def _classify(self, report: dict[str, Any]) -> dict[str, str]:
        """Attribute the most likely owner/category of an audit failure."""
        if report.get("error"):
            return self._attribution(
                "HARNESS_INTERNAL_ERROR",
                "audit service raised an internal error",
                "harness",
            )
        if report.get("passed") is True:
            return self._attribution("NONE", "no failure detected", "none")

        failed_results = [
            item
            for item in (report.get("api_validation", {}) or {}).get("results", [])
            if not item.get("passed")
        ]
        if failed_results:
            if any(self._looks_like_test_data_dependency(item) for item in failed_results):
                return self._attribution(
                    "TEST_DATA_DEPENDENCY_ERROR",
                    "API tests failed because later requests used unresolved or missing resource IDs",
                    "test_suite",
                )
            return self._attribution(
                "API_CONTRACT_MISMATCH",
                "API response did not satisfy the generated contract assertions",
                "project_or_api_doc",
            )

        for step in report.get("steps", []) or []:
            if step.get("status") not in {"failed", "timeout"}:
                continue
            name = str(step.get("name") or "")
            stdout = str(step.get("stdout_tail") or "")
            stderr = str(step.get("stderr_tail") or "")
            combined = f"{stdout}\n{stderr}"
            if self._looks_like_sandbox_infra_error(combined):
                return self._attribution(
                    "HARNESS_INTERNAL_ERROR",
                    "sandbox or Docker execution failed before project evidence could be collected",
                    "harness",
                )
            if "dependency image" in combined.lower():
                return self._attribution(
                    "DEPENDENCY_ERROR",
                    "dependency image preparation failed",
                    "project_dependency",
                )
            if "AUDIT_SETUP_FAILED" in stdout or "alembic" in combined.lower():
                return self._attribution(
                    "DB_MIGRATION_ERROR",
                    "database setup command failed before service startup",
                    "project_setup",
                )
            if "app.mock_llm" in combined or "app.api_testing" in combined:
                return self._attribution(
                    "HARNESS_INTERNAL_ERROR",
                    "harness runner module could not be loaded inside sandbox",
                    "harness",
                )
            if name == "api_acceptance":
                return self._attribution(
                    "SERVICE_STARTUP_ERROR",
                    "audited service did not start or acceptance runner failed before API results were produced",
                    "project_runtime",
                )
            return self._attribution(
                "PROJECT_CODE_ERROR",
                f"check step failed: {name}",
                "project_code",
            )
        return self._attribution("NONE", "no failure detected", "none")

    @staticmethod
    def _looks_like_sandbox_infra_error(combined: str) -> bool:
        """Detect Docker SDK/daemon failures that are not project-code errors."""
        lower = combined.lower()
        return any(
            marker in lower
            for marker in (
                "unixhttpconnectionpool",
                "docker operation failed",
                "docker.errors",
                "read timed out. (read timeout=",
            )
        )

    @staticmethod
    def _looks_like_test_data_dependency(result: dict[str, Any]) -> bool:
        """Detect failures caused by unresolved chained resource identifiers."""
        excerpt = str(result.get("response_excerpt") or "").lower()
        failures = " ".join(str(item).lower() for item in result.get("failures", []))
        return (
            "not found" in excerpt
            and ("missing json path" in failures or "expected type" in failures)
        )

    @staticmethod
    def _attribution(category: str, reason: str, owner: str) -> dict[str, str]:
        """Build a stable failure attribution payload."""
        return {"category": category, "reason": reason, "owner": owner}

    @staticmethod
    def _grade(score: int) -> str:
        """Map numeric legacy score to a letter grade."""
        if score >= 90:
            return "A"
        if score >= 75:
            return "B"
        if score >= 60:
            return "C"
        if score >= 40:
            return "D"
        return "F"
