from app.services.report_service import ReportService


def test_markdown_report_contains_summary_and_failure_details():
    report = {
        "audit_id": "audit-1",
        "audit_mode": "full",
        "llm_policy": "mock",
        "status": "failed",
        "risk_level": "high",
        "repo": "/project",
        "profile": "python-basic",
        "created_at": "2026-06-22T00:00:00Z",
        "completed_at": "2026-06-22T00:01:00Z",
        "dependency_image": {
            "image": "audit-harness/python-deps:abc",
            "dependency_file": "requirements.txt",
            "fingerprint": "abc",
            "cache_hit": True,
        },
        "project_memory": {
            "loaded_files": ["PROJECT.md", "KNOWN_ISSUES.md"],
            "known_issue_count": 1,
            "warnings": [],
        },
        "steps": [
            {
                "name": "api_acceptance",
                "status": "failed",
                "exit_code": 1,
                "duration_ms": 100,
                "image": "audit-harness/python-deps:abc",
            }
        ],
        "api_validation": {
            "generated_tests": 1,
            "passed_tests": 0,
            "failed_tests": 1,
            "warnings": ["SSE endpoint"],
            "results": [
                {
                    "name": "stream test",
                    "passed": False,
                    "status_code": 200,
                    "duration_ms": 10,
                    "failures": ["SSE error event: failed"],
                    "response_excerpt": 'data: {"type":"error"}',
                }
            ],
        },
        "artifacts": {"report_json": "/tmp/report.json"},
        "error": None,
    }

    markdown = ReportService().render_markdown(report)

    assert "# Audit Harness 审计报告" in markdown
    assert "生成测试 | 1" in markdown
    assert "SSE error event: failed" in markdown
    assert 'data: {"type":"error"}' in markdown
