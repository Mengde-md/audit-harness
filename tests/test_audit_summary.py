from app.schemas.audit_summary import AuditSummary


def test_audit_summary_normalizes_partial_json_payload():
    summary = AuditSummary.model_validate(
        {
            "llm_policy": "mock",
            "api_validation": {
                "generated_tests": 3,
                "passed_tests": 1,
            },
            "project_memory": {
                "loaded_files": ["PROJECT.md"],
            },
        }
    )

    payload = summary.to_json_dict()

    assert payload["llm_policy"] == "mock"
    assert payload["api_validation"]["generated_tests"] == 3
    assert payload["api_validation"]["failed_tests"] == 0
    assert payload["dependency_image"]["image"] == ""
    assert payload["project_memory"]["known_issue_count"] == 0
