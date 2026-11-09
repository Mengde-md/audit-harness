"""Confidence evaluation for domain scores."""

from typing import Any


class ConfidenceEvaluator:
    """Estimate how trustworthy and stable the quality judgment is."""

    def evaluate(
        self,
        report: dict[str, Any],
        metrics: dict[str, Any],
    ) -> dict[str, Any]:
        """Return confidence score, level, and reasons."""
        score = 1.0
        reasons: list[str] = []
        score = self._deduct(score, metrics["api_total"] == 0, 0.35, "No API tests were generated", reasons)
        score = self._deduct(score, 0 < metrics["api_total"] < 5, 0.20, "API test count is low", reasons)
        score = self._deduct(score, metrics["api_failed"] > 0 and not metrics["api_has_results"], 0.10, "API failures lack per-test result details", reasons)
        if metrics["api_warning_count"]:
            deduction = min(0.15, metrics["api_warning_count"] * 0.05)
            score -= deduction
            reasons.append("API document or generated suite has warnings")
        score = self._deduct(score, not metrics["has_acceptance_result"], 0.15, "Acceptance result artifact is missing", reasons)
        score = self._deduct(score, report.get("llm_policy") == "mock" and metrics["llm_mock_call_count"] == 0, 0.10, "Mock LLM policy enabled but no mock calls were observed", reasons)
        score = self._deduct(score, "code_review" not in report, 0.10, "Code review result is missing", reasons)
        score = self._deduct(score, metrics["has_internal_error"], 0.25, "Harness internal error reduces score reliability", reasons)
        normalized = max(0.0, min(1.0, round(score, 2)))
        return {
            "score": normalized,
            "level": self._level(normalized),
            "reasons": reasons,
        }

    @staticmethod
    def _deduct(
        score: float,
        condition: bool,
        points: float,
        reason: str,
        reasons: list[str],
    ) -> float:
        """Apply one confidence deduction when the condition is true."""
        if condition:
            reasons.append(reason)
            return score - points
        return score

    @staticmethod
    def _level(score: float) -> str:
        """Map confidence score to high, medium, or low."""
        if score >= 0.8:
            return "high"
        if score >= 0.55:
            return "medium"
        return "low"
