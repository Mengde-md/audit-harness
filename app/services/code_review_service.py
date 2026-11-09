"""Code review service combining deterministic rules and patch-level LLM review."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from app.code_review.models import CodeReviewResult, LlmReviewMeta, ReviewFinding
from app.code_review.patch_scope import extract_python_files_from_patch
from app.code_review.scanner import CodeReviewScanner
from app.core.config import Settings
from app.providers.llm.base import LlmProvider
from app.schemas.audit import AuditCreateRequest
from app.utils.time import utc_now


class CodeReviewService:
    """Run review checks and write standalone review artifacts."""

    def __init__(
        self,
        *,
        settings: Settings,
        llm_provider: LlmProvider | None = None,
        scanner: CodeReviewScanner | None = None,
    ):
        self.settings = settings
        self.llm_provider = llm_provider
        self.scanner = scanner or CodeReviewScanner()
        self.logger = logging.getLogger("audit_harness.audit")

    async def review(
        self,
        *,
        workspace: Path,
        audit_root: Path,
        request: AuditCreateRequest,
    ) -> tuple[CodeReviewResult, Path, Path]:
        """Run deterministic review and optional change-mode LLM patch review."""
        scoped_files = (
            extract_python_files_from_patch(request.patch)
            if request.audit_mode == "change"
            else None
        )
        files = self.scanner.collect_files(workspace, scoped_files=scoped_files)
        result = CodeReviewResult(
            audit_mode=request.audit_mode,
            scanned_files=[self._relative(path, workspace) for path in files],
            deterministic_rule_count=self.scanner.rule_count,
        )
        if request.audit_mode == "change" and request.patch and not files:
            result.warnings.append("change patch contains no existing Python files to scan")

        result.findings.extend(
            await asyncio.to_thread(self.scanner.scan, workspace, files)
        )
        if request.audit_mode == "change":
            await self._review_patch_with_llm(result, request)
        else:
            result.llm_review = LlmReviewMeta(
                enabled=False,
                skipped_reason="full mode disables LLM patch review",
                model=self.settings.llm_model,
            )

        json_path = audit_root / "code_review_result.json"
        markdown_path = audit_root / "code_review_report.md"
        await asyncio.to_thread(
            json_path.write_text,
            json.dumps(result.to_summary(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        await asyncio.to_thread(
            markdown_path.write_text,
            self.render_markdown(result),
            encoding="utf-8",
        )
        self.logger.info(
            "CODE_REVIEW_DONE | mode=%s | files=%s | findings=%s | llm_enabled=%s",
            request.audit_mode,
            len(result.scanned_files),
            result.counts["total"],
            result.llm_review.enabled,
        )
        return result, json_path, markdown_path

    async def _review_patch_with_llm(
        self,
        result: CodeReviewResult,
        request: AuditCreateRequest,
    ) -> None:
        """Ask an LLM to review only the submitted patch in change mode."""
        if self.llm_provider is None:
            result.llm_review = LlmReviewMeta(
                enabled=False,
                skipped_reason="LLM provider is not configured",
                model=self.settings.llm_model,
            )
            return
        if not self.settings.llm_api_key or not self.settings.llm_model:
            result.llm_review = LlmReviewMeta(
                enabled=False,
                skipped_reason="LLM_API_KEY or LLM_MODEL is not configured",
                model=self.settings.llm_model,
            )
            return

        result.llm_review = LlmReviewMeta(enabled=True, model=self.settings.llm_model)
        try:
            payload = await self.llm_provider.generate_json(
                system_prompt=self._llm_system_prompt(),
                user_prompt=self._llm_user_prompt(request, result),
            )
        except Exception as exc:  # noqa: BLE001 - review must not fail the audit.
            result.llm_review.warning = f"{type(exc).__name__}: {exc}"
            result.warnings.append(f"LLM patch review skipped after error: {exc}")
            self.logger.warning(
                "CODE_REVIEW_LLM_FAILED | error_type=%s | error=%s",
                type(exc).__name__,
                exc,
            )
            return

        result.findings.extend(self._parse_llm_findings(payload))
        result.test_suggestions.extend(self._parse_test_suggestions(payload))

    def render_markdown(self, result: CodeReviewResult) -> str:
        """Render the standalone code review report."""
        counts = result.counts
        lines = [
            "# Code Review 报告",
            "",
            "## 概览",
            "",
            "| 字段 | 值 |",
            "| --- | --- |",
            f"| 生成时间 | `{utc_now()}` |",
            f"| 审计模式 | `{result.audit_mode}` |",
            f"| 扫描文件数 | {len(result.scanned_files)} |",
            f"| 确定性规则数 | {result.deterministic_rule_count} |",
            f"| LLM Patch Review | `{result.llm_review.enabled}` |",
            f"| LLM 跳过原因 | `{result.llm_review.skipped_reason or ''}` |",
            f"| Finding 总数 | {counts['total']} |",
            f"| 高危 | {counts['high']} |",
            f"| 中危 | {counts['medium']} |",
            f"| 低危 | {counts['low']} |",
            "",
            "## 扫描范围",
            "",
        ]
        if result.scanned_files:
            lines.extend(f"- `{item}`" for item in result.scanned_files)
        else:
            lines.append("- 无 Python 文件进入 review 范围")
        lines.append("")

        if result.warnings:
            lines.extend(["## 警告", ""])
            lines.extend(f"- {self._escape(item)}" for item in result.warnings)
            lines.append("")

        lines.extend(["## Review Findings", ""])
        if not result.findings:
            lines.extend(["暂无发现。", ""])
        else:
            lines.extend(
                [
                    "| 严重级别 | 来源 | 规则 | 文件 | 行号 | 问题 | 建议 |",
                    "| --- | --- | --- | --- | ---: | --- | --- |",
                ]
            )
            for item in sorted(
                result.findings,
                key=lambda finding: (
                    {"high": 0, "medium": 1, "low": 2}[finding.severity],
                    finding.file,
                    finding.line,
                ),
            ):
                lines.append(
                    "| "
                    f"`{item.severity}` | "
                    f"`{item.source}` | "
                    f"`{item.rule_id}` | "
                    f"`{self._escape(item.file)}` | "
                    f"{item.line} | "
                    f"{self._escape(item.message)} | "
                    f"{self._escape(item.suggestion)} |"
                )
            lines.append("")

        if result.test_suggestions:
            lines.extend(["## 建议补充测试", ""])
            lines.extend(f"- {self._escape(item)}" for item in result.test_suggestions)
            lines.append("")
        return "\n".join(lines)

    def _llm_system_prompt(self) -> str:
        """Prompt the LLM to produce strict JSON review output."""
        return (
            "You are a senior code reviewer for AI agent backend projects. "
            "Review only the supplied patch, do not invent files outside it. "
            "Focus on logic regressions, missing validation, concurrency, data "
            "consistency, security risk, and missing tests. Return strict JSON "
            "with fields: findings and test_suggestions. findings is an array "
            "of objects containing severity(high|medium|low), category, file, "
            "line, message, suggestion, evidence."
        )

    def _llm_user_prompt(
        self,
        request: AuditCreateRequest,
        result: CodeReviewResult,
    ) -> str:
        """Build a bounded patch-review prompt."""
        deterministic = [
            item.model_dump(mode="json")
            for item in result.findings[:30]
            if item.source == "deterministic"
        ]
        patch = (request.patch or "")[: self.settings.max_patch_chars]
        return json.dumps(
            {
                "audit_mode": request.audit_mode,
                "base_commit": request.base_commit,
                "deterministic_findings": deterministic,
                "patch": patch,
            },
            ensure_ascii=False,
            indent=2,
        )

    def _parse_llm_findings(self, payload: dict[str, Any]) -> list[ReviewFinding]:
        """Normalize best-effort LLM finding payloads."""
        findings: list[ReviewFinding] = []
        for index, item in enumerate(payload.get("findings", []) or [], start=1):
            if not isinstance(item, dict):
                continue
            severity = item.get("severity")
            if severity not in {"high", "medium", "low"}:
                severity = "medium"
            try:
                findings.append(
                    ReviewFinding(
                        source="llm",
                        rule_id=f"LLM-REVIEW-{index:03d}",
                        severity=severity,
                        category=str(item.get("category") or "patch_review"),
                        file=str(item.get("file") or "patch"),
                        line=max(int(item.get("line") or 1), 1),
                        message=str(item.get("message") or "LLM review finding"),
                        suggestion=str(item.get("suggestion") or "Review this patch manually."),
                        evidence=(
                            str(item.get("evidence"))
                            if item.get("evidence") is not None
                            else None
                        ),
                    )
                )
            except (TypeError, ValueError):
                continue
        return findings

    @staticmethod
    def _parse_test_suggestions(payload: dict[str, Any]) -> list[str]:
        """Normalize LLM test suggestion payloads."""
        suggestions = payload.get("test_suggestions", []) or []
        if not isinstance(suggestions, list):
            return []
        return [str(item) for item in suggestions if str(item).strip()][:20]

    @staticmethod
    def _relative(path: Path, root: Path) -> str:
        """Format file paths relative to the audited project root."""
        try:
            return str(path.relative_to(root))
        except ValueError:
            return str(path)

    @staticmethod
    def _escape(value: Any) -> str:
        """Escape Markdown table-breaking characters."""
        return str(value or "").replace("|", "\\|").replace("\n", " ")

