"""Markdown renderer for human-readable audit reports."""

from typing import Any


class ReportMarkdownRenderer:
    """Render structured report dictionaries into Markdown sections."""

    def render(self, report: dict[str, Any]) -> str:
        """Render the full report in stable section order."""
        lines: list[str] = []
        self._append_overview(lines, report)
        self._append_quality(lines, report)
        self._append_code_review(lines, report)
        self._append_resume(lines, report)
        self._append_recovery(lines, report)
        self._append_project_config(lines, report)
        self._append_steps(lines, report)
        self._append_api_validation(lines, report)
        self._append_error(lines, report)
        self._append_artifacts(lines, report)
        return "\n".join(lines)

    def _append_overview(self, lines: list[str], report: dict[str, Any]) -> None:
        """Append task metadata and high-level status."""
        lines.extend(
            [
                "# Audit Harness 审计报告",
                "",
                "## 任务概览",
                "",
                "| 字段 | 值 |",
                "| --- | --- |",
                f"| Audit ID | `{report.get('audit_id', '')}` |",
                f"| 审计模式 | `{report.get('audit_mode', '')}` |",
                f"| LLM 策略 | `{report.get('llm_policy', '')}` |",
                f"| 状态 | `{report.get('status', '')}` |",
                f"| 风险等级 | `{report.get('risk_level', '')}` |",
                f"| 项目 | `{self._escape(report.get('repo'))}` |",
                f"| Profile | `{report.get('profile', '')}` |",
                f"| 创建时间 | `{report.get('created_at', '')}` |",
                f"| 完成时间 | `{report.get('completed_at', '')}` |",
                "",
            ]
        )

    def _append_quality(self, lines: list[str], report: dict[str, Any]) -> None:
        """Append quality intelligence, legacy score, and raw metrics."""
        quality = report.get("quality", {})
        attribution = quality.get("failure_attribution", {})
        metrics = quality.get("metrics", {})
        confidence = quality.get("confidence", {}) or {}
        gate = quality.get("gate", {}) or {}
        legacy = quality.get("legacy", {}) or {}
        lines.extend(
            [
                "## 智能评估引擎",
                "",
                "| 字段 | 值 |",
                "| --- | --- |",
                f"| Gate | `{gate.get('status', '')}` |",
                f"| Confidence | `{confidence.get('level', '')}` ({confidence.get('score', 0)}) |",
                f"| Legacy 总分 | {legacy.get('score', quality.get('score', 0))} |",
                f"| Legacy 等级 | `{legacy.get('grade', quality.get('grade', ''))}` |",
                f"| 失败归因 | `{attribution.get('category', '')}` |",
                f"| 归因说明 | {self._escape(attribution.get('reason', ''))} |",
                f"| 责任域 | `{attribution.get('owner', '')}` |",
                "",
                "### 大项评分",
                "",
                "| 大项 | 分数 | 等级 | 状态 | 是否建议优化 |",
                "| --- | ---: | --- | --- | --- |",
            ]
        )
        for name, item in (quality.get("domains", {}) or {}).items():
            needs_optimization = "是" if item.get("needs_optimization") else "否"
            lines.append(
                f"| {self._escape(name)} | "
                f"{item.get('score', 0)} | "
                f"`{item.get('grade', '')}` | "
                f"`{item.get('status', '')}` | "
                f"{needs_optimization} |"
            )
        penalties = self._top_penalties(quality)
        if penalties:
            lines.extend(
                [
                    "",
                    "### 主要扣分项",
                    "",
                    "| 大项 | 规则 | 扣分 | 原因 |",
                    "| --- | --- | ---: | --- |",
                ]
            )
            for item in penalties:
                lines.append(
                    "| "
                    f"`{item.get('domain', '')}` | "
                    f"`{item.get('rule_id', '')}` | "
                    f"{item.get('points', 0)} | "
                    f"{self._escape(item.get('reason', ''))} |"
                )
        if gate.get("reasons"):
            lines.extend(["", "### Gate 原因", ""])
            for reason in gate.get("reasons", []):
                lines.append(f"- `{self._escape(reason)}`")
        if confidence.get("reasons"):
            lines.extend(["", "### Confidence 原因", ""])
            for reason in confidence.get("reasons", []):
                lines.append(f"- {self._escape(reason)}")
        lines.extend(
            [
                "",
                "### 链路指标",
                "",
                "| 指标 | 值 |",
                "| --- | --- |",
                f"| 步骤总耗时(ms) | {metrics.get('total_step_duration_ms', 0)} |",
                f"| 依赖镜像缓存命中 | `{metrics.get('dependency_cache_hit', metrics.get('dependency_image_cache_hit'))}` |",
                f"| 初始化成功 | `{metrics.get('setup_succeeded')}` |",
                f"| 服务启动成功 | `{metrics.get('service_started')}` |",
                f"| API 总数 | {metrics.get('api_total', 0)} |",
                f"| API 通过 | {metrics.get('api_passed', 0)} |",
                f"| API 失败 | {metrics.get('api_failed', 0)} |",
                f"| Mock LLM 调用数 | {metrics.get('llm_mock_call_count', 0)} |",
                f"| Review Finding 总数 | {metrics.get('review_total_count', metrics.get('code_review_total', 0))} |",
                f"| Review 高危 | {metrics.get('review_high_count', metrics.get('code_review_high', 0))} |",
                f"| Review 中危 | {metrics.get('review_medium_count', metrics.get('code_review_medium', 0))} |",
                "",
            ]
        )

    @staticmethod
    def _top_penalties(quality: dict[str, Any]) -> list[dict[str, Any]]:
        """Return top penalties across all domains."""
        penalties: list[dict[str, Any]] = []
        for domain in (quality.get("domains", {}) or {}).values():
            penalties.extend(domain.get("penalties", []) or [])
        return sorted(penalties, key=lambda item: item.get("points", 0))[:5]

    def _append_code_review(self, lines: list[str], report: dict[str, Any]) -> None:
        """Append deterministic and LLM code review summary."""
        review = report.get("code_review", {}) or {}
        counts = review.get("counts", {}) or {}
        llm_review = review.get("llm_review", {}) or {}
        lines.extend(
            [
                "## Code Review",
                "",
                "| 字段 | 值 |",
                "| --- | --- |",
                f"| 扫描文件数 | {len(review.get('scanned_files', []))} |",
                f"| 确定性规则数 | {review.get('deterministic_rule_count', 0)} |",
                f"| LLM Patch Review | `{llm_review.get('enabled', False)}` |",
                f"| LLM 跳过原因 | `{self._escape(llm_review.get('skipped_reason', ''))}` |",
                f"| Finding 总数 | {counts.get('total', 0)} |",
                f"| 高危 | {counts.get('high', 0)} |",
                f"| 中危 | {counts.get('medium', 0)} |",
                f"| 低危 | {counts.get('low', 0)} |",
                "",
            ]
        )
        findings = review.get("findings", []) or []
        if not findings:
            return
        lines.extend(
            [
                "### Review 明细",
                "",
                "| 严重级别 | 来源 | 规则 | 文件 | 行号 | 问题 |",
                "| --- | --- | --- | --- | ---: | --- |",
            ]
        )
        for item in findings[:20]:
            lines.append(
                "| "
                f"`{item.get('severity')}` | "
                f"`{item.get('source')}` | "
                f"`{item.get('rule_id')}` | "
                f"`{self._escape(item.get('file'))}` | "
                f"{item.get('line')} | "
                f"{self._escape(item.get('message'))} |"
            )
        lines.append("")

    def _append_recovery(self, lines: list[str], report: dict[str, Any]) -> None:
        """Append self-healing retry decision and attempts."""
        recovery = report.get("recovery", {})
        decision = recovery.get("decision") or {}
        lines.extend(
            [
                "### 自愈重试",
                "",
                "| 字段 | 值 |",
                "| --- | --- |",
                f"| 启用 | `{recovery.get('enabled')}` |",
                f"| 是否重试 | `{decision.get('retry')}` |",
                f"| 失败分类 | `{decision.get('category', '')}` |",
                f"| 错误模式 | `{decision.get('error_pattern', '')}` |",
                f"| 动作 | `{decision.get('action', '')}` |",
                f"| 记忆来源 | `{decision.get('memory_source', '')}` |",
                f"| 重试预算 | `{decision.get('retry_budget', 0)}` |",
                f"| 候选动作 | `{', '.join(decision.get('candidate_actions', []))}` |",
                f"| 原因 | {self._escape(decision.get('reason', ''))} |",
                "",
            ]
        )
        guidance = decision.get("guidance", []) or []
        if guidance:
            lines.extend(
                [
                    "#### 记忆参考",
                    "",
                    "| 强度 | 范围 | 信任等级 | 动作 | 提示 |",
                    "| --- | --- | --- | --- | --- |",
                ]
            )
            for item in guidance:
                lines.append(
                    "| "
                    f"{self._escape(item.get('section', ''))} | "
                    f"`{self._escape(item.get('memory_source', ''))}` | "
                    f"`{self._escape(item.get('trust_level', ''))}` | "
                    f"`{self._escape(item.get('action', ''))}` | "
                    f"{self._escape(item.get('message', ''))} |"
                )
            lines.append("")
        attempts = recovery.get("attempts", [])
        if not attempts:
            return
        lines.extend(
            [
                "| 阶段 | 分类 | 错误模式 | 动作 | 结果 | 耗时(ms) |",
                "| --- | --- | --- | --- | --- | ---: |",
            ]
        )
        for attempt in attempts:
            lines.append(
                "| "
                f"{self._escape(attempt.get('stage', ''))} | "
                f"`{self._escape(attempt.get('category', ''))}` | "
                f"`{self._escape(attempt.get('error_pattern', ''))}` | "
                f"`{self._escape(attempt.get('action', ''))}` | "
                f"`{self._escape(attempt.get('result', ''))}` | "
                f"{attempt.get('duration_ms', 0)} |"
            )
        lines.append("")

    def _append_resume(self, lines: list[str], report: dict[str, Any]) -> None:
        """Append resumable execution evidence."""
        resume = report.get("resume", {})
        if not resume:
            return
        lines.extend(
            [
                "### 恢复执行",
                "",
                "| 字段 | 值 |",
                "| --- | --- |",
                f"| 启用 | `{resume.get('enabled')}` |",
                f"| 是否恢复任务 | `{resume.get('resumed')}` |",
                f"| 中断步骤数 | {len(resume.get('interrupted_steps', []))} |",
                f"| 跳过步骤数 | {len(resume.get('skipped_steps', []))} |",
                "",
            ]
        )
        skipped_steps = resume.get("skipped_steps", [])
        if skipped_steps:
            lines.extend(["| 跳过步骤 | 原因 |", "| --- | --- |"])
            for step in skipped_steps:
                lines.append(
                    f"| {self._escape(step.get('name', ''))} "
                    f"| {self._escape(step.get('reason', ''))} |"
                )
            lines.append("")

    def _append_project_config(
        self,
        lines: list[str],
        report: dict[str, Any],
    ) -> None:
        """Append project config, dependency image, services, and memory summary."""
        dependency = report.get("dependency_image", {})
        runtime = report.get("runtime", {})
        project_memory = report.get("project_memory", {})
        warnings = report.get("project_config_warnings", [])
        lines.extend(
            [
                "## 项目配置",
                "",
                "| 字段 | 值 |",
                "| --- | --- |",
                f"| 配置警告数 | {len(warnings)} |",
                "",
                "## 运行时",
                "",
                "| 字段 | 值 |",
                "| --- | --- |",
                f"| 语言 | `{runtime.get('language', '')}` |",
                f"| 版本 | `{runtime.get('version', '')}` |",
                f"| 镜像 | `{runtime.get('image', '')}` |",
                f"| 来源 | `{runtime.get('source', '')}` |",
                "",
                "## 依赖镜像",
                "",
                "| 字段 | 值 |",
                "| --- | --- |",
                f"| 镜像 | `{dependency.get('image', '')}` |",
                f"| 依赖文件 | `{dependency.get('dependency_file', '')}` |",
                f"| 指纹 | `{dependency.get('fingerprint', '')}` |",
                f"| 缓存命中 | `{dependency.get('cache_hit', '')}` |",
                "",
                "## 托管依赖服务",
                "",
                "| 字段 | 值 |",
                "| --- | --- |",
                f"| 服务配置 | `{self._escape(report.get('services'))}` |",
                f"| 初始化命令数 | {report.get('setup_command_count', 0)} |",
                "",
                "## 项目记忆",
                "",
                "| 字段 | 值 |",
                "| --- | --- |",
                f"| 已加载文件 | `{', '.join(project_memory.get('loaded_files', []))}` |",
                f"| 已知问题数 | {project_memory.get('known_issue_count', 0)} |",
                f"| 警告数 | {len(project_memory.get('warnings', []))} |",
                "",
            ]
        )
        if warnings:
            lines.extend(["### 项目配置警告", ""])
            lines.extend(f"- {self._escape(item)}" for item in warnings)
            lines.append("")

    def _append_steps(self, lines: list[str], report: dict[str, Any]) -> None:
        """Append sandbox step table."""
        lines.extend(
            [
                "## 执行步骤",
                "",
                "| 步骤 | 状态 | 退出码 | 耗时(ms) | 镜像 |",
                "| --- | --- | ---: | ---: | --- |",
            ]
        )
        for step in report.get("steps", []):
            lines.append(
                f"| {self._escape(step.get('name'))} "
                f"| `{step.get('status')}` "
                f"| {step.get('exit_code')} "
                f"| {step.get('duration_ms')} "
                f"| `{self._escape(step.get('image'))}` |"
            )
        lines.append("")

    def _append_api_validation(
        self,
        lines: list[str],
        report: dict[str, Any],
    ) -> None:
        """Append API acceptance summary, warnings, and per-test details."""
        api = report.get("api_validation", {})
        lines.extend(
            [
                "## API 验收",
                "",
                "| 指标 | 数量 |",
                "| --- | ---: |",
                f"| 生成测试 | {api.get('generated_tests', 0)} |",
                f"| 通过 | {api.get('passed_tests', 0)} |",
                f"| 失败 | {api.get('failed_tests', 0)} |",
                f"| 测试集来源 | {api.get('test_suite_source', '')} |",
                f"| 缓存命中 | {api.get('cache_hit', False)} |",
                "",
            ]
        )
        warnings = api.get("warnings", [])
        if warnings:
            lines.extend(["### 文档解析警告", ""])
            lines.extend(f"- {self._escape(item)}" for item in warnings)
            lines.append("")
        results = api.get("results", [])
        if results:
            self._append_test_results(lines, results)
            self._append_failures(lines, results)

    def _append_test_results(
        self,
        lines: list[str],
        results: list[dict[str, Any]],
    ) -> None:
        """Append the compact pass/fail table for each generated API test."""
        lines.extend(
            [
                "### 测试结果",
                "",
                "| 测试 | 状态 | HTTP | 耗时(ms) |",
                "| --- | --- | ---: | ---: |",
            ]
        )
        for result in results:
            status = "PASS" if result.get("passed") else "FAIL"
            lines.append(
                f"| {self._escape(result.get('name'))} "
                f"| `{status}` "
                f"| {result.get('status_code')} "
                f"| {result.get('duration_ms')} |"
            )
        lines.append("")

    def _append_failures(
        self,
        lines: list[str],
        results: list[dict[str, Any]],
    ) -> None:
        """Append detailed failure messages and response excerpts."""
        failures = [item for item in results if not item.get("passed")]
        if not failures:
            return
        lines.extend(["### 失败详情", ""])
        for index, result in enumerate(failures, start=1):
            lines.extend([f"#### {index}. {self._escape(result.get('name'))}", ""])
            for failure in result.get("failures", []):
                lines.append(f"- {self._escape(failure)}")
            excerpt = result.get("response_excerpt")
            if excerpt:
                lines.extend(["", "```text", str(excerpt)[:4000], "```"])
            lines.append("")

    def _append_error(self, lines: list[str], report: dict[str, Any]) -> None:
        """Append top-level audit service errors, if any."""
        error = report.get("error")
        if not error:
            return
        lines.extend(
            [
                "## 系统错误",
                "",
                f"- 错误码：`{error.get('code')}`",
                f"- 类型：`{error.get('type')}`",
                f"- 信息：{self._escape(error.get('message'))}",
                "",
            ]
        )

    def _append_artifacts(self, lines: list[str], report: dict[str, Any]) -> None:
        """Append all artifact paths registered for the audit."""
        lines.extend(["## 产物", ""])
        for artifact_type, artifact_path in report.get("artifacts", {}).items():
            lines.append(f"- `{artifact_type}`: `{artifact_path}`")
        lines.append("")

    @staticmethod
    def _escape(value: Any) -> str:
        """Escape Markdown table-breaking characters."""
        return str(value or "").replace("|", "\\|").replace("\n", " ")
