from app.services.report_service import ReportService


def test_markdown_table_cells_flatten_windows_and_legacy_line_endings():
    report = {"quality": {"failure_attribution": {"reason": "first\r\nsecond\rthird | detail"}}}

    markdown = ReportService().render_markdown(report)

    assert r"| 归因说明 | first second third \| detail |" in markdown
    assert "\r" not in markdown


def test_step_table_distinguishes_missing_measurements_from_zero():
    report = {
        "steps": [
            {"name": "queued", "status": "pending"},
            {"name": "completed", "status": "passed", "exit_code": 0, "duration_ms": 0},
        ]
    }

    markdown = ReportService().render_markdown(report)

    assert "| queued | `pending` | — | — |" in markdown
    assert "| completed | `passed` | 0 | 0 |" in markdown


def test_response_excerpts_with_code_fences_stay_in_one_code_block():
    for inner_fence in ("```", "````"):
        excerpt = f"response:\n{inner_fence}json\n{{}}\n{inner_fence}\nend"
        report = {
            "api_validation": {
                "results": [{"name": "failed request", "passed": False, "response_excerpt": excerpt}]
            }
        }

        markdown = ReportService().render_markdown(report)

        outer_fence = inner_fence + "`"
        assert f"\n{outer_fence}text\n{excerpt}\n{outer_fence}\n" in markdown


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
