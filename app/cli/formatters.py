"""Human-readable output formatters for the Audit Harness CLI."""

import json
from typing import Any


def format_json(data: dict[str, Any]) -> str:
    """Return stable pretty JSON."""
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


def format_audit_submitted(data: dict[str, Any]) -> str:
    """Format create-audit response."""
    return "\n".join(
        [
            "Audit submitted",
            f"audit_id: {data.get('audit_id', '')}",
            f"status: {data.get('status', '')}",
        ]
    )


def format_status(data: dict[str, Any]) -> str:
    """Format audit status response."""
    summary = data.get("summary", {}) or {}
    quality = summary.get("quality", {}) or {}
    gate = (quality.get("gate", {}) or {}).get("status", "")
    passed_steps = summary.get("passed_steps", 0)
    failed_steps = summary.get("failed_steps", 0)
    return "\n".join(
        [
            "Audit Status",
            f"audit_id: {data.get('audit_id', '')}",
            f"mode: {data.get('audit_mode', '')}",
            f"status: {data.get('status', '')}",
            f"profile: {data.get('profile', '')}",
            f"passed_steps: {passed_steps}",
            f"failed_steps: {failed_steps}",
            f"gate: {gate}",
        ]
    )


def format_watch_tick(data: dict[str, Any], elapsed_seconds: int) -> str:
    """Format one watch polling line."""
    status = str(data.get("status", ""))
    step = _latest_step(data)
    if step:
        detail = f"{step.get('name', '')} {step.get('status', '')}"
    else:
        detail = "no steps"
    minutes, seconds = divmod(elapsed_seconds, 60)
    return f"[{minutes:02d}:{seconds:02d}] {status:<8} {detail}"


def format_watch_completed(data: dict[str, Any]) -> str:
    """Format final watch result."""
    summary = data.get("summary", {}) or {}
    quality = summary.get("quality", {}) or {}
    gate = quality.get("gate", {}) or {}
    confidence = quality.get("confidence", {}) or {}
    artifacts = summary.get("artifacts", {}) or {}
    return "\n".join(
        [
            "Audit completed",
            f"status: {data.get('status', '')}",
            f"gate: {gate.get('status', '')}",
            f"confidence: {confidence.get('level', '')} ({confidence.get('score', 0)})",
            f"report: {artifacts.get('report_markdown', '')}",
        ]
    )


def format_report(data: dict[str, Any], path_only: bool = False) -> str:
    """Format structured report response."""
    artifacts = data.get("artifacts", {}) or {}
    if path_only:
        return str(artifacts.get("report_markdown") or artifacts.get("report_json") or "")
    quality = data.get("quality", {}) or {}
    domains = quality.get("domains", {}) or {}
    gate = quality.get("gate", {}) or {}
    lines = [
        "Audit Report",
        f"audit_id: {data.get('audit_id', '')}",
        f"status: {data.get('status', '')}",
        f"gate: {gate.get('status', '')}",
    ]
    for name in ("runtime", "code_quality", "api_test", "agent"):
        domain = domains.get(name, {}) or {}
        if domain:
            lines.append(
                f"{name}: {domain.get('score', 0)}/100 {domain.get('status', '')}"
            )
    lines.extend(
        [
            f"report_md: {artifacts.get('report_markdown', '')}",
            f"report_json: {artifacts.get('report_json', '')}",
        ]
    )
    return "\n".join(lines)


def is_terminal_status(status: str) -> bool:
    """Return true when an audit task no longer needs polling."""
    return status in {"passed", "failed", "error", "cancelled"}


def _latest_step(data: dict[str, Any]) -> dict[str, Any] | None:
    """Return the most recent step in status payload."""
    steps = data.get("steps", []) or []
    return steps[-1] if steps else None
