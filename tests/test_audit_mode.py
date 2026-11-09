import pytest
from pydantic import ValidationError

from app.schemas.audit import AuditCreateRequest


def base_request() -> dict:
    return {
        "requester_id": "agent-test",
        "repo": "/tmp/project",
        "llm_policy": "mock",
        "api_document_markdown": "# Health\nGET /health",
        "startup_command": "uvicorn app.main:app --host 0.0.0.0 --port 8000",
        "service_port": 8000,
        "profile": "python-basic",
    }


def test_change_mode_requires_commit_and_patch():
    with pytest.raises(ValidationError, match="requires both base_commit and patch"):
        AuditCreateRequest(**base_request(), audit_mode="change")


def test_full_mode_rejects_change_inputs():
    with pytest.raises(ValidationError, match="requires base_commit and patch to be omitted"):
        AuditCreateRequest(
            **base_request(),
            audit_mode="full",
            base_commit="abc123",
        )


def test_explicit_modes_are_accepted():
    full = AuditCreateRequest(**base_request(), audit_mode="full")
    change = AuditCreateRequest(
        **base_request(),
        audit_mode="change",
        base_commit="abc123",
        patch="diff --git a/a.py b/a.py\n",
    )

    assert full.audit_mode == "full"
    assert change.audit_mode == "change"


def test_managed_services_accept_fixed_first_version_options():
    parsed = AuditCreateRequest(
        **base_request(),
        audit_mode="full",
        services={
            "postgres": {"enabled": True, "variant": "pg16-vector"},
            "redis": {"enabled": True},
        },
        setup_commands=["cd backend && alembic upgrade head"],
    )

    assert parsed.services.postgres.enabled is True
    assert parsed.services.postgres.variant == "pg16-vector"
    assert parsed.services.redis.enabled is True
    assert parsed.setup_commands == ["cd backend && alembic upgrade head"]


def test_managed_services_reject_arbitrary_postgres_variant():
    with pytest.raises(ValidationError):
        AuditCreateRequest(
            **base_request(),
            audit_mode="full",
            services={
                "postgres": {
                    "enabled": True,
                    "variant": "postgres:latest",
                }
            },
        )


def test_mock_rule_ids_must_be_unique():
    rule = {
        "id": "same",
        "match": {"contains_any": ["prd"]},
        "response": {"format": "text", "content": "ok"},
    }

    with pytest.raises(ValidationError, match="ids must be unique"):
        AuditCreateRequest(
            **base_request(),
            audit_mode="full",
            llm_mock_rules=[rule, rule],
        )


def test_disabled_policy_rejects_explicit_mock_config():
    request = base_request()
    request["llm_policy"] = "disabled"

    with pytest.raises(ValidationError, match="requires mock profile"):
        AuditCreateRequest(
            **request,
            audit_mode="full",
            llm_mock_profile="generic-agent",
        )


def test_disabled_policy_without_mock_config_is_accepted():
    request = base_request()
    request["llm_policy"] = "disabled"

    parsed = AuditCreateRequest(**request, audit_mode="full")

    assert parsed.llm_policy == "disabled"
