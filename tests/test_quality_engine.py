from app.services.quality_service import QualityService


def _base_report() -> dict:
    return {
        "error": None,
        "passed": True,
        "llm_policy": "mock",
        "dependency_image": {"cache_hit": True},
        "setup_command_count": 0,
        "artifacts": {
            "report_json": "missing-report.json",
            "report_markdown": "missing-report.md",
            "execution_events_jsonl": "missing-events.jsonl",
            "acceptance_result_json": "missing-acceptance.json",
            "api_schema_json": "missing-schema.json",
            "acceptance_suite_json": "missing-suite.json",
            "code_review_report_markdown": "missing-review.md",
        },
        "steps": [
            {"name": "python_compile", "status": "passed", "duration_ms": 100},
            {
                "name": "api_acceptance",
                "status": "passed",
                "duration_ms": 200,
                "stdout_tail": "Application startup complete\n",
                "stderr_tail": "",
            },
        ],
        "api_validation": {
            "generated_tests": 6,
            "passed_tests": 6,
            "failed_tests": 0,
            "warnings": [],
            "results": [
                {"name": f"case-{index}", "passed": True, "duration_ms": 1}
                for index in range(6)
            ],
        },
        "code_review": {
            "counts": {"total": 0, "high": 0, "medium": 0, "low": 0},
            "findings": [],
        },
        "recovery": {"attempts": []},
    }


def test_quality_engine_passes_when_critical_domains_are_clean():
    quality = QualityService().build(_base_report())

    assert quality["gate"]["status"] == "pass"
    assert quality["domains"]["runtime"]["status"] == "pass"
    assert quality["domains"]["api_test"]["status"] == "pass"
    assert quality["domains"]["code_quality"]["status"] == "pass"
    assert quality["domains"]["agent"]["status"] == "pass"


def test_quality_engine_fails_when_service_does_not_start_and_caps_api():
    report = _base_report()
    report["passed"] = False
    report["steps"][1]["status"] = "failed"
    report["steps"][1]["stdout_tail"] = ""

    quality = QualityService().build(report)

    assert quality["gate"]["status"] == "fail"
    assert "service_started=false" in quality["gate"]["blocking_reasons"]
    assert quality["domains"]["api_test"]["score"] <= 20


def test_quality_engine_records_api_penalty_details():
    report = _base_report()
    report["passed"] = False
    report["api_validation"]["passed_tests"] = 4
    report["api_validation"]["failed_tests"] = 2
    report["api_validation"]["results"][0]["passed"] = False

    quality = QualityService().build(report)

    penalties = quality["domains"]["api_test"]["penalties"]
    assert any(item["rule_id"] == "API_TEST_FAILED" for item in penalties)
    assert quality["domains"]["api_test"]["needs_optimization"] is True


def test_quality_engine_review_high_finding_fails_gate():
    report = _base_report()
    report["code_review"] = {
        "counts": {"total": 1, "high": 1, "medium": 0, "low": 0},
        "findings": [
            {
                "severity": "high",
                "rule_id": "SEC-001",
                "message": "Hardcoded secret",
            }
        ],
    }

    quality = QualityService().build(report)

    assert quality["gate"]["status"] == "fail"
    assert "review_high_count>0" in quality["gate"]["blocking_reasons"]


def test_quality_engine_low_api_count_reduces_confidence():
    report = _base_report()
    report["api_validation"]["generated_tests"] = 2
    report["api_validation"]["passed_tests"] = 2
    report["api_validation"]["results"] = [
        {"name": "case-1", "passed": True},
        {"name": "case-2", "passed": True},
    ]

    quality = QualityService().build(report)

    assert quality["confidence"]["level"] in {"medium", "low"}
    assert any("API test count is low" in item for item in quality["confidence"]["reasons"])
    assert quality["domains"]["api_test"]["score"] <= 60


def test_quality_engine_missing_artifacts_deducts_agent_domain():
    report = _base_report()
    report["artifacts"] = {}

    quality = QualityService().build(report)

    assert quality["domains"]["agent"]["score"] < 100
    assert any(
        item["rule_id"] == "ARTIFACT_INCOMPLETE"
        for item in quality["domains"]["agent"]["penalties"]
    )


def test_quality_service_keeps_legacy_contract():
    quality = QualityService().build(_base_report())

    assert "score" in quality
    assert "grade" in quality
    assert "dimensions" in quality
    assert "api" in quality["dimensions"]
    assert quality["legacy"]["score"] == quality["score"]
