"""Quality gate evaluation."""

from typing import Any


class GateEvaluator:
    """Convert metrics, domain scores, and confidence into a release decision."""

    def evaluate(
        self,
        metrics: dict[str, Any],
        domains: dict[str, dict[str, Any]],
        confidence: dict[str, Any],
    ) -> dict[str, Any]:
        """Return pass, conditional_pass, or fail with reasons."""
        blocking_reasons = self._blocking_reasons(metrics, domains, confidence)
        if blocking_reasons:
            return {
                "status": "fail",
                "reasons": blocking_reasons,
                "blocking_reasons": blocking_reasons,
                "required_actions": self._required_actions(blocking_reasons),
            }
        conditional_reasons = self._conditional_reasons(metrics, domains, confidence)
        if conditional_reasons:
            return {
                "status": "conditional_pass",
                "reasons": conditional_reasons,
                "blocking_reasons": [],
                "required_actions": self._required_actions(conditional_reasons),
            }
        return {
            "status": "pass",
            "reasons": [
                "all critical domains passed",
                "confidence is high",
                "no high-severity review finding",
                "all API tests passed",
            ],
            "blocking_reasons": [],
            "required_actions": [],
        }

    @staticmethod
    def _blocking_reasons(
        metrics: dict[str, Any],
        domains: dict[str, dict[str, Any]],
        confidence: dict[str, Any],
    ) -> list[str]:
        """Collect hard fail reasons."""
        reasons: list[str] = []
        if metrics["has_internal_error"]:
            reasons.append("has_internal_error=true")
        if not metrics["service_started"]:
            reasons.append("service_started=false")
        if not metrics["dependency_ready"]:
            reasons.append("dependency_ready=false")
        if metrics["api_total"] > 0 and metrics["api_pass_rate"] < 0.5:
            reasons.append("api_pass_rate<0.5")
        if metrics["review_high_count"] > 0:
            reasons.append("review_high_count>0")
        failed_domains = [
            name for name, item in domains.items() if item.get("status") == "fail"
        ]
        if failed_domains:
            reasons.append(f"domain_status=fail:{','.join(failed_domains)}")
        if confidence["score"] < 0.35:
            reasons.append("confidence.score<0.35")
        return reasons

    @staticmethod
    def _conditional_reasons(
        metrics: dict[str, Any],
        domains: dict[str, dict[str, Any]],
        confidence: dict[str, Any],
    ) -> list[str]:
        """Collect conditional pass reasons."""
        reasons: list[str] = []
        conditional_domains = [
            name
            for name, item in domains.items()
            if item.get("status") == "conditional_pass"
        ]
        if conditional_domains:
            reasons.append(f"domain_status=conditional_pass:{','.join(conditional_domains)}")
        if confidence["level"] == "medium":
            reasons.append("confidence.level=medium")
        if metrics["api_failed"] > 0:
            reasons.append("api_failed>0")
        if metrics["review_medium_count"] > 0:
            reasons.append("review_medium_count>0")
        if metrics["api_warning_count"] > 0:
            reasons.append("api_warning_count>0")
        if metrics["recovery_attempt_count"] > 0:
            reasons.append("recovery_attempt_count>0")
        return reasons

    @staticmethod
    def _required_actions(reasons: list[str]) -> list[str]:
        """Translate gate reasons into compact next actions."""
        actions: list[str] = []
        for reason in reasons:
            if "service_started" in reason:
                actions.append("修复服务启动失败，再重新执行 API 验收")
            elif "dependency_ready" in reason:
                actions.append("检查依赖镜像、requirements 和 setup command")
            elif "api_" in reason:
                actions.append("查看 API 验收报告，修复失败用例或补充接口文档")
            elif "review_" in reason or "code_quality" in reason:
                actions.append("处理 Code Review 高中危 finding")
            elif "confidence" in reason:
                actions.append("补齐 acceptance result、trace 和 review 证据，提高评分可信度")
            elif "agent" in reason:
                actions.append("补齐报告、事件、LLM trace 等 Agent 工程产物")
        return list(dict.fromkeys(actions))
