from app.services.quality_service import QualityService


def test_quality_service_classifies_test_data_dependency_error():
    report = {
        "error": None,
        "dependency_image": {"cache_hit": True},
        "artifacts": {
            "report_json": "missing.json",
            "execution_events_jsonl": "missing.jsonl",
            "acceptance_result_json": "missing-acceptance.json",
        },
        "steps": [
            {
                "name": "python_compile",
                "status": "passed",
                "duration_ms": 100,
            },
            {
                "name": "api_acceptance",
                "status": "failed",
                "duration_ms": 200,
                "stdout_tail": (
                    "AUDIT_SETUP_DONE\n"
                    "Application startup complete\n"
                ),
                "stderr_tail": "",
            },
        ],
        "api_validation": {
            "generated_tests": 3,
            "passed_tests": 1,
            "failed_tests": 2,
            "results": [
                {
                    "name": "upload",
                    "passed": False,
                    "duration_ms": 3,
                    "failures": ["missing JSON path: data.document_id"],
                    "response_excerpt": (
                        '{"code":40001,"msg":"knowledge base not found","data":null}'
                    ),
                }
            ],
        },
    }

    quality = QualityService().build(report)

    assert quality["failure_attribution"]["category"] == "TEST_DATA_DEPENDENCY_ERROR"
    assert quality["failure_attribution"]["owner"] == "test_suite"
    assert quality["metrics"]["api_passed"] == 1
    assert quality["dimensions"]["api"] == 8


def test_quality_service_scores_code_review_risk():
    report = {
        "error": None,
        "passed": True,
        "dependency_image": {"cache_hit": True},
        "artifacts": {"report_json": "missing.json"},
        "steps": [
            {
                "name": "python_compile",
                "status": "passed",
                "duration_ms": 100,
            },
            {
                "name": "api_acceptance",
                "status": "passed",
                "duration_ms": 200,
                "stdout_tail": "AUDIT_SETUP_DONE\nApplication startup complete\n",
                "stderr_tail": "",
            },
        ],
        "api_validation": {
            "generated_tests": 4,
            "passed_tests": 4,
            "failed_tests": 0,
            "results": [],
        },
        "code_review": {
            "counts": {
                "total": 3,
                "high": 1,
                "medium": 1,
                "low": 1,
            }
        },
    }

    quality = QualityService().build(report)

    assert quality["dimensions"]["code_review"] == 5
    assert quality["metrics"]["code_review_high"] == 1
    assert quality["metrics"]["code_review_medium"] == 1
    assert quality["metrics"]["code_review_low"] == 1


def test_quality_service_classifies_docker_api_timeout_as_harness_error():
    report = {
        "error": None,
        "passed": False,
        "dependency_image": {"cache_hit": True},
        "artifacts": {},
        "steps": [
            {
                "name": "python_compile",
                "status": "failed",
                "duration_ms": None,
                "stdout_tail": "",
                "stderr_tail": (
                    "UnixHTTPConnectionPool(host='localhost', port=None): "
                    "Read timed out. (read timeout=60)"
                ),
            }
        ],
        "api_validation": {
            "generated_tests": 0,
            "passed_tests": 0,
            "failed_tests": 0,
            "results": [],
        },
    }

    quality = QualityService().build(report)

    assert quality["failure_attribution"]["category"] == "HARNESS_INTERNAL_ERROR"
    assert quality["failure_attribution"]["owner"] == "harness"
