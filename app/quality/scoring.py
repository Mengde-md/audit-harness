"""Domain scoring for the Quality Intelligence Engine."""

from typing import Any

from app.quality.models import DomainScore, Penalty
from app.quality.rubric import grade, status


class ScoringEngine:
    """Apply deterministic deduction rules to each quality domain."""

    def score(
        self,
        report: dict[str, Any],
        metrics: dict[str, Any],
    ) -> dict[str, dict[str, Any]]:
        """Return independent 100-point scores for all first-stage domains."""
        return {
            "runtime": self._runtime(metrics).to_dict(),
            "code_quality": self._code_quality(metrics).to_dict(),
            "api_test": self._api_test(metrics).to_dict(),
            "agent": self._agent(report, metrics).to_dict(),
        }

    def _runtime(self, metrics: dict[str, Any]) -> DomainScore:
        """Score service startup, dependencies, setup, and basic checks."""
        penalties: list[Penalty] = []
        if metrics["has_internal_error"]:
            penalties.append(self._penalty("HARNESS_INTERNAL_ERROR", "runtime", -100, "Harness internal error was detected"))
        if not metrics["dependency_ready"]:
            penalties.append(self._penalty("DEPENDENCY_NOT_READY", "runtime", -35, "Dependency environment was not ready"))
        if not metrics["service_started"]:
            penalties.append(self._penalty("SERVICE_NOT_STARTED", "runtime", -30, "Audited service did not emit startup-complete signal"))
        if not metrics["setup_succeeded"]:
            penalties.append(self._penalty("SETUP_FAILED", "runtime", -20, "Project setup command did not complete successfully"))
        if metrics["check_total"] == 0:
            penalties.append(self._penalty("NO_BASIC_CHECKS", "runtime", -15, "No basic check step was available"))
        elif metrics["check_pass_rate"] < 1:
            points = -round((1 - metrics["check_pass_rate"]) * 25)
            penalties.append(self._penalty("BASIC_CHECK_FAILED", "runtime", points, "Some basic check steps failed"))
        if metrics["stderr_has_traceback"]:
            penalties.append(self._penalty("TRACEBACK_IN_STDERR", "runtime", -10, "Failed step stderr contains Python traceback"))
        return self._domain(
            "runtime",
            penalties,
            "Runtime, dependencies, setup, and basic checks",
            ["优先修复依赖、启动、setup command 或基础 check 失败问题"],
        )

    def _code_quality(self, metrics: dict[str, Any]) -> DomainScore:
        """Score deterministic and LLM review findings."""
        penalties: list[Penalty] = []
        high = metrics["review_high_count"]
        medium = metrics["review_medium_count"]
        low = metrics["review_low_count"]
        if high:
            penalties.append(self._penalty("REVIEW_HIGH_FINDING", "code_quality", -min(100, high * 50), f"{high} high-severity review finding(s)"))
        if medium:
            penalties.append(self._penalty("REVIEW_MEDIUM_FINDING", "code_quality", -min(60, medium * 15), f"{medium} medium-severity review finding(s)"))
        if low:
            penalties.append(self._penalty("REVIEW_LOW_FINDING", "code_quality", -min(30, low * 3), f"{low} low-severity review finding(s)"))
        return self._domain(
            "code_quality",
            penalties,
            "Code quality and review risk",
            ["优先处理 high / medium Review finding，再治理低危可维护性问题"],
        )

    def _api_test(self, metrics: dict[str, Any]) -> DomainScore:
        """Score API acceptance coverage and pass rate."""
        penalties: list[Penalty] = []
        if metrics["api_total"] == 0:
            penalties.append(self._penalty("NO_API_TESTS", "api_test", -60, "No generated API tests were available"))
        else:
            failed_ratio = 1 - metrics["api_pass_rate"]
            if failed_ratio > 0:
                penalties.append(self._penalty("API_TEST_FAILED", "api_test", -round(failed_ratio * 70), f"{metrics['api_failed']} API test(s) failed"))
            if metrics["api_total"] < 5:
                penalties.append(self._penalty("LOW_API_COVERAGE", "api_test", -15, "API test count is too small for stable judgment"))
        if metrics["api_warning_count"]:
            penalties.append(self._penalty("API_GENERATION_WARNING", "api_test", -min(15, metrics["api_warning_count"] * 5), "API document or generated suite has warnings"))
        if metrics["api_total"] > 0 and not metrics["api_has_results"]:
            penalties.append(self._penalty("MISSING_API_RESULTS", "api_test", -10, "API suite did not produce per-test result details"))
        score = self._score_from(penalties)
        if not metrics["service_started"]:
            score = min(score, 20)
            penalties.append(self._penalty("SERVICE_NOT_EVALUABLE", "api_test", 0, "Service did not start; API score is capped at 20"))
        if 0 < metrics["api_total"] < 5:
            score = min(score, 60)
        return self._domain(
            "api_test",
            penalties,
            "API contract acceptance quality",
            ["补齐关键链路 API 验收用例，并提升响应结构和关键字段断言强度"],
            score_override=score,
        )

    def _agent(self, report: dict[str, Any], metrics: dict[str, Any]) -> DomainScore:
        """Score agent engineering maturity: traces, artifacts, and recovery."""
        penalties: list[Penalty] = []
        missing_ratio = 1 - metrics["artifact_completeness"]
        if missing_ratio:
            penalties.append(self._penalty("ARTIFACT_INCOMPLETE", "agent", -round(missing_ratio * 35), "Key report or trace artifacts are incomplete"))
        if not metrics["has_acceptance_result"]:
            penalties.append(self._penalty("MISSING_ACCEPTANCE_RESULT", "agent", -15, "Acceptance result artifact is missing"))
        if report.get("llm_policy") == "mock" and not metrics["llm_mock_trace_available"]:
            penalties.append(self._penalty("MISSING_LLM_TRACE", "agent", -10, "Mock LLM trace artifact is unavailable"))
        if report.get("llm_policy") == "mock" and metrics["llm_mock_call_count"] == 0:
            penalties.append(self._penalty("NO_LLM_MOCK_CALL", "agent", -10, "Mock LLM policy enabled but no mock call was observed"))
        if metrics["recovery_attempt_count"] and not metrics["recovery_success"]:
            penalties.append(self._penalty("RECOVERY_NOT_SUCCESSFUL", "agent", -10, "Self-healing attempts were made but did not succeed"))
        return self._domain(
            "agent",
            penalties,
            "Agent engineering maturity and audit operability",
            ["补齐报告、事件、API 结果、LLM trace 等 artifact，提升审计可追溯性"],
        )

    def _domain(
        self,
        name: str,
        penalties: list[Penalty],
        summary_prefix: str,
        default_suggestions: list[str],
        score_override: int | None = None,
    ) -> DomainScore:
        """Build one domain score from penalties."""
        score = self._score_from(penalties) if score_override is None else score_override
        score = max(0, min(100, score))
        domain_status = status(score)
        return DomainScore(
            name=name,
            max_score=100,
            score=score,
            grade=grade(score),
            status=domain_status,
            needs_optimization=domain_status != "pass" or bool(penalties),
            summary=f"{summary_prefix}: {score}/100",
            penalties=penalties,
            suggestions=default_suggestions if penalties else [],
        )

    @staticmethod
    def _score_from(penalties: list[Penalty]) -> int:
        """Apply deductions and floor the score at zero."""
        return max(0, 100 + sum(item.points for item in penalties))

    @staticmethod
    def _penalty(
        rule_id: str,
        domain: str,
        points: int,
        reason: str,
    ) -> Penalty:
        """Create a penalty. Evidence ids can be attached in later iterations."""
        return Penalty(rule_id=rule_id, domain=domain, points=points, reason=reason)
