import json
from pathlib import Path

import pytest

from app.code_review.patch_scope import extract_python_files_from_patch
from app.code_review.scanner import CodeReviewScanner
from app.core.config import Settings
from app.schemas.audit import AuditCreateRequest
from app.services.code_review_service import CodeReviewService


class FakeReviewLlmProvider:
    async def generate_json(self, *, system_prompt: str, user_prompt: str) -> dict:
        payload = json.loads(user_prompt)
        assert payload["audit_mode"] == "change"
        assert "diff --git" in payload["patch"]
        return {
            "findings": [
                {
                    "severity": "medium",
                    "category": "test_gap",
                    "file": "app/service.py",
                    "line": 3,
                    "message": "Patch changes request behavior without a regression test.",
                    "suggestion": "Add an API test covering the new branch.",
                    "evidence": "+    return requests.get(url)",
                }
            ],
            "test_suggestions": ["Add a timeout regression test."],
        }


def test_patch_scope_extracts_changed_python_files():
    patch = (
        "diff --git a/app/service.py b/app/service.py\n"
        "diff --git a/README.md b/README.md\n"
        "diff --git a/app/old.py b/dev/null\n"
    )

    assert extract_python_files_from_patch(patch) == [Path("app/service.py")]


def test_deterministic_scanner_detects_security_and_reliability_rules(tmp_path):
    source_root = tmp_path / "repo"
    app_dir = source_root / "app"
    app_dir.mkdir(parents=True)
    (app_dir / "service.py").write_text(
        "\n".join(
            [
                "import requests",
                "import subprocess",
                "API_TOKEN = 'hardcoded-token-value'",
                "def run(url, cursor, user_id):",
                "    data = eval('1 + 1')",
                "    subprocess.run('ls -la', shell=True)",
                "    requests.get(url)",
                "    cursor.execute(f'SELECT * FROM users WHERE id={user_id}')",
                "    try:",
                "        return data",
                "    except Exception:",
                "        pass",
            ]
        ),
        encoding="utf-8",
    )

    scanner = CodeReviewScanner()
    files = scanner.collect_files(source_root)
    findings = scanner.scan(source_root, files)
    rule_ids = {item.rule_id for item in findings}

    assert {
        "PY-SEC-001",
        "PY-SEC-002",
        "PY-SEC-004",
        "PY-SEC-005",
        "PY-REL-001",
        "PY-REL-002",
    }.issubset(rule_ids)


@pytest.mark.asyncio
async def test_code_review_service_uses_llm_only_for_change_mode(tmp_path):
    workspace = tmp_path / "audit" / "workspace"
    app_dir = workspace / "app"
    app_dir.mkdir(parents=True)
    (app_dir / "service.py").write_text(
        "import requests\n\ndef fetch(url):\n    return requests.get(url)\n",
        encoding="utf-8",
    )
    settings = Settings(
        workspace_root=tmp_path / "workspaces",
        llm_api_key="test-key",
        llm_model="test-model",
    )
    service = CodeReviewService(
        settings=settings,
        llm_provider=FakeReviewLlmProvider(),
    )
    request = AuditCreateRequest(
        requester_id="review-test",
        repo=str(workspace),
        audit_mode="change",
        base_commit="abc123",
        patch=(
            "diff --git a/app/service.py b/app/service.py\n"
            "+    return requests.get(url)\n"
        ),
        llm_policy="mock",
        api_document_markdown="# Health\nGET /health",
        startup_command="uvicorn main:app --host 0.0.0.0 --port 8000",
        service_port=8000,
    )

    result, json_path, markdown_path = await service.review(
        workspace=workspace,
        audit_root=workspace.parent,
        request=request,
    )

    assert result.llm_review.enabled is True
    assert result.counts["total"] == 2
    assert any(item.source == "llm" for item in result.findings)
    assert "Add a timeout regression test." in result.test_suggestions
    assert json_path.exists()
    assert markdown_path.exists()


@pytest.mark.asyncio
async def test_code_review_service_skips_llm_for_full_mode(tmp_path):
    workspace = tmp_path / "audit" / "workspace"
    app_dir = workspace / "app"
    app_dir.mkdir(parents=True)
    (app_dir / "service.py").write_text("print('ok')\n", encoding="utf-8")
    settings = Settings(workspace_root=tmp_path / "workspaces")
    service = CodeReviewService(
        settings=settings,
        llm_provider=FakeReviewLlmProvider(),
    )
    request = AuditCreateRequest(
        requester_id="review-test",
        repo=str(workspace),
        audit_mode="full",
        llm_policy="mock",
        api_document_markdown="# Health\nGET /health",
        startup_command="uvicorn main:app --host 0.0.0.0 --port 8000",
        service_port=8000,
    )

    result, _, _ = await service.review(
        workspace=workspace,
        audit_root=workspace.parent,
        request=request,
    )

    assert result.llm_review.enabled is False
    assert result.llm_review.skipped_reason == "full mode disables LLM patch review"
    assert all(item.source == "deterministic" for item in result.findings)
