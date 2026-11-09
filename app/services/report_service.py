"""Structured report builder and report file writer."""

import json
from pathlib import Path
from typing import Any

from app.models.audit_job import AuditJob
from app.schemas.audit_summary import AuditSummary
from app.services.report_markdown_renderer import ReportMarkdownRenderer
from app.services.quality_service import QualityService


class ReportService:
    """Convert persisted audit models into JSON/Markdown report artifacts."""

    def build(self, job: AuditJob) -> dict[str, Any]:
        """Build the structured report consumed by APIs and Markdown rendering."""
        artifacts = {item.artifact_type: item.path for item in job.artifacts}
        summary = AuditSummary.model_validate(job.summary or {})
        api_validation = summary.api_validation.model_dump(mode="json")
        steps = [
            {
                "step_id": step.id,
                "name": step.name,
                "command": step.command,
                "status": step.status,
                "image": step.image,
                "container_id": step.container_id,
                "exit_code": step.exit_code,
                "duration_ms": step.duration_ms,
                "stdout_tail": step.stdout_tail,
                "stderr_tail": step.stderr_tail,
                "findings": step.findings,
                "metadata": step.metadata_json,
            }
            for step in sorted(job.steps, key=lambda item: item.started_at or job.created_at)
        ]
        report = {
            "audit_id": job.id,
            "audit_mode": job.audit_mode,
            "llm_policy": summary.llm_policy,
            "status": job.status,
            "passed": job.status == "passed",
            "risk_level": job.risk_level,
            "repo": job.repo,
            "base_commit": job.base_commit,
            "profile": job.profile,
            "api_validation": api_validation,
            "code_review": getattr(summary, "code_review", {}),
            "project_config_warnings": summary.project_config_warnings,
            "runtime": summary.runtime.model_dump(mode="json"),
            "dependency_image": summary.dependency_image.model_dump(mode="json"),
            "dependency_config": summary.dependency_config,
            "env_files": summary.env_files,
            "llm_adapters": summary.llm_adapters,
            "project_memory": summary.project_memory.model_dump(mode="json"),
            "recovery": summary.recovery,
            "resume": getattr(summary, "resume", {}),
            "services": summary.services,
            "setup_command_count": summary.setup_command_count,
            "steps": steps,
            "artifacts": artifacts,
            "sandbox": {
                "backend": job.sandbox_backend,
                "image": job.sandbox_image,
            },
            "error": (
                {
                    "code": summary.error_code,
                    "type": job.error_type,
                    "message": job.error_message,
                }
                if job.error_type
                else None
            ),
            "created_at": job.created_at,
            "completed_at": job.completed_at,
        }
        # Quality is computed last because it depends on normalized report fields,
        # not directly on ORM objects.
        report["quality"] = QualityService().build(report)
        return report

    def write(self, report: dict[str, Any], path: Path) -> None:
        """Write the JSON report with stable formatting."""
        path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

    def write_markdown(self, report: dict[str, Any], path: Path) -> None:
        """Write the human-readable Markdown report."""
        path.write_text(self.render_markdown(report), encoding="utf-8")

    def render_markdown(self, report: dict[str, Any]) -> str:
        """Render Markdown through the dedicated renderer component."""
        return ReportMarkdownRenderer().render(report)
