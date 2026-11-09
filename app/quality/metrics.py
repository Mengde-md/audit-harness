"""Metric extraction for the Quality Intelligence Engine."""

from pathlib import Path
from typing import Any


class MetricExtractor:
    """Convert raw report facts into stable, domain-oriented metrics."""

    def extract(
        self,
        report: dict[str, Any],
        attribution: dict[str, Any],
    ) -> dict[str, Any]:
        """Extract all first-stage quality metrics."""
        steps = report.get("steps", []) or []
        api = report.get("api_validation", {}) or {}
        artifacts = report.get("artifacts", {}) or {}
        acceptance = self._acceptance_step(report)
        stdout = str(acceptance.get("stdout_tail") or "") if acceptance else ""
        stderr = str(acceptance.get("stderr_tail") or "") if acceptance else ""
        check_steps = [
            step
            for step in steps
            if not str(step.get("name") or "").startswith("api_acceptance")
        ]
        check_total = len(check_steps)
        check_passed = sum(1 for step in check_steps if step.get("status") == "passed")
        api_total = int(api.get("generated_tests") or 0)
        api_passed = int(api.get("passed_tests") or 0)
        api_results = api.get("results", []) or []
        review_counts = (report.get("code_review", {}) or {}).get("counts", {}) or {}
        setup_command_count = int(report.get("setup_command_count", 0) or 0)
        llm_trace_path = artifacts.get("llm_call_traces_jsonl")
        required_artifacts = (
            "report_json",
            "report_markdown",
            "execution_events_jsonl",
            "acceptance_result_json",
            "api_schema_json",
            "acceptance_suite_json",
            "code_review_report_markdown",
        )
        artifact_hits = sum(1 for item in required_artifacts if artifacts.get(item))
        api_failed_result_count = sum(
            1 for item in api_results if item.get("passed") is False
        )
        recovery_attempts = (report.get("recovery", {}) or {}).get("attempts", []) or []
        metrics = {
            "total_step_duration_ms": sum(
                int(step.get("duration_ms") or 0) for step in steps
            ),
            "step_durations_ms": {
                step.get("name"): step.get("duration_ms") for step in steps
            },
            "dependency_ready": bool(report.get("dependency_image"))
            and attribution.get("category") != "DEPENDENCY_ERROR"
            and not self._has_dependency_error(report),
            "dependency_cache_hit": bool(
                (report.get("dependency_image", {}) or {}).get("cache_hit")
            ),
            "check_total": check_total,
            "check_passed": check_passed,
            "check_pass_rate": check_passed / check_total if check_total else 0,
            "service_started": "Application startup complete" in stdout,
            "setup_succeeded": setup_command_count == 0 or "AUDIT_SETUP_DONE" in stdout,
            "setup_failed": "AUDIT_SETUP_FAILED" in stdout,
            "api_total": api_total,
            "api_passed": api_passed,
            "api_failed": int(api.get("failed_tests") or 0),
            "api_pass_rate": api_passed / api_total if api_total else 0,
            "api_warning_count": len(api.get("warnings", []) or []),
            "api_has_results": bool(api_results),
            "api_failed_result_count": api_failed_result_count,
            "review_total_count": int(review_counts.get("total") or 0),
            "review_high_count": int(review_counts.get("high") or 0),
            "review_medium_count": int(review_counts.get("medium") or 0),
            "review_low_count": int(review_counts.get("low") or 0),
            "artifact_completeness": artifact_hits / len(required_artifacts),
            "has_report_json": bool(artifacts.get("report_json")),
            "has_execution_events": bool(artifacts.get("execution_events_jsonl")),
            "has_acceptance_result": bool(artifacts.get("acceptance_result_json")),
            "llm_mock_call_count": self._count_jsonl(llm_trace_path),
            "llm_mock_trace_available": bool(llm_trace_path)
            and Path(str(llm_trace_path)).exists(),
            "recovery_attempt_count": len(recovery_attempts),
            "recovery_success": any(
                str(item.get("result") or item.get("status") or "").lower()
                in {"passed", "success", "succeeded"}
                for item in recovery_attempts
            ),
            "has_internal_error": bool(report.get("error"))
            or attribution.get("category") == "HARNESS_INTERNAL_ERROR",
            "stderr_has_traceback": "Traceback" in stderr,
            "llm_policy": report.get("llm_policy"),
        }
        metrics.update(self._compatibility_aliases(metrics, report))
        return metrics

    @staticmethod
    def _acceptance_step(report: dict[str, Any]) -> dict[str, Any] | None:
        """Return the latest API acceptance step, including retry attempts."""
        steps = [
            step
            for step in report.get("steps", []) or []
            if str(step.get("name") or "").startswith("api_acceptance")
        ]
        return steps[-1] if steps else None

    @staticmethod
    def _count_jsonl(path: str | None) -> int:
        """Count non-empty lines in a JSONL artifact."""
        if not path:
            return 0
        jsonl_path = Path(str(path))
        if not jsonl_path.exists():
            return 0
        return sum(
            1
            for line in jsonl_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )

    @staticmethod
    def _has_dependency_error(report: dict[str, Any]) -> bool:
        """Detect dependency-related internal errors."""
        error = report.get("error") or {}
        text = f"{error.get('type', '')} {error.get('message', '')}".lower()
        return "dependency" in text

    @staticmethod
    def _compatibility_aliases(
        metrics: dict[str, Any],
        report: dict[str, Any],
    ) -> dict[str, Any]:
        """Keep old metric names available for existing callers."""
        results = (report.get("api_validation", {}) or {}).get("results", []) or []
        slowest_api = max(results, key=lambda item: item.get("duration_ms") or 0) if results else None
        return {
            "dependency_image_cache_hit": metrics["dependency_cache_hit"],
            "code_review_total": metrics["review_total_count"],
            "code_review_high": metrics["review_high_count"],
            "code_review_medium": metrics["review_medium_count"],
            "code_review_low": metrics["review_low_count"],
            "slowest_api": (
                {
                    "name": slowest_api.get("name"),
                    "duration_ms": slowest_api.get("duration_ms"),
                    "passed": slowest_api.get("passed"),
                }
                if slowest_api
                else None
            ),
        }
