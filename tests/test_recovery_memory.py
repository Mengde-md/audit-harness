"""Recovery memory persistence, promotion, and policy tests."""

import json

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.db.base import Base
from app.memory.recovery_memory import RecoveryAdvisor, RecoveryMemoryStore
from app.models.cross_recovery_candidate import CrossRecoveryCandidate
from app.models.cross_recovery_experience import CrossRecoveryExperience
from app.models.project_recovery_memory import ProjectRecoveryMemory
from app.services.recovery_actions import RecoveryActionRegistry, RecoveryPolicy


@pytest.fixture
async def recovery_context(tmp_path):
    database_path = tmp_path / "memory.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{database_path}",
        recovery_memory_root=tmp_path / "legacy",
        workspace_root=tmp_path / "workspaces",
    )
    async with session_factory() as session:
        store = RecoveryMemoryStore(session, settings)
        yield store, RecoveryAdvisor(store), settings, session
    await engine.dispose()


def build_signature(advisor: RecoveryAdvisor, **overrides):
    values = {
        "stage": "api_acceptance",
        "category": "SERVICE_READY_ERROR",
        "error_pattern": "service_not_ready",
        "profile": "python-basic",
        "services": {},
        "setup_commands": [],
        "startup_command": "uvicorn app.main:app --host 0.0.0.0 --port 8000",
    }
    values.update(overrides)
    return advisor.build_signature(**values)


@pytest.mark.asyncio
async def test_project_success_memory_promotes_to_high(recovery_context):
    store, advisor, _, session = recovery_context
    signature = build_signature(advisor)

    for index in range(3):
        record = await store.record_success(
            repo="/tmp/demo",
            signature=signature,
            action="rerun_acceptance_with_same_inputs",
            audit_id=f"audit-{index}",
            duration_ms=100,
            runtime="python",
            framework=None,
            services=[],
        )

    assert record["success_count"] == 3
    assert record["trust_level"] == "HIGH"
    rows = list((await session.execute(select(ProjectRecoveryMemory))).scalars())
    assert len(rows) == 1
    assert rows[0].scope == "single_repo"
    assert rows[0].source == "system"


@pytest.mark.asyncio
async def test_cross_recovery_experience_promotes_and_is_recalled(recovery_context):
    store, advisor, _, session = recovery_context
    signature = build_signature(advisor)
    action = "rerun_acceptance_with_same_inputs"

    for repo_index in range(5):
        for success_index in range(4):
            await store.record_success(
                repo=f"/tmp/repo-{repo_index}",
                signature=signature,
                action=action,
                audit_id=f"audit-{repo_index}-{success_index}",
                duration_ms=100,
                runtime="python",
                framework=None,
                services=[],
            )

    candidates = list(
        (await session.execute(select(CrossRecoveryCandidate))).scalars()
    )
    experiences = list(
        (await session.execute(select(CrossRecoveryExperience))).scalars()
    )
    assert len(candidates) == 1
    assert candidates[0].repo_count == 5
    assert candidates[0].success_count == 20
    assert len(experiences) == 1
    assert experiences[0].trust_level == "HIGH"
    assert experiences[0].repo_hashes == candidates[0].repo_hashes

    recommendation = await advisor.recommend(
        repo="/tmp/new-repository",
        signature=signature,
    )
    assert recommendation["memory_source"] == "cross_recovery"
    assert recommendation["action"] == action
    assert recommendation["actions"][0]["trust_level"] == "HIGH"
    assert recommendation["guidance"][0]["section"] == "重点参考"


@pytest.mark.asyncio
async def test_project_memory_has_priority_over_cross_experience(recovery_context):
    store, advisor, _, _ = recovery_context
    signature = build_signature(advisor)
    cross_action = "rerun_acceptance_with_same_inputs"
    project_action = "rerun_acceptance_once"

    for repo_index in range(3):
        for success_index in range(4):
            await store.record_success(
                repo=f"/tmp/shared-{repo_index}",
                signature=signature,
                action=cross_action,
                audit_id=f"shared-{repo_index}-{success_index}",
                duration_ms=100,
                runtime="python",
                framework=None,
                services=[],
            )
    await store.record_success(
        repo="/tmp/current",
        signature=signature,
        action=project_action,
        audit_id="current-1",
        duration_ms=100,
        runtime="python",
        framework=None,
        services=[],
    )

    recommendation = await advisor.recommend(
        repo="/tmp/current",
        signature=signature,
    )

    assert recommendation["memory_source"] == "project_recovery"
    assert recommendation["action"] == project_action
    assert all(
        item["memory_source"] != "cross_recovery"
        for item in recommendation["actions"]
    )


def test_recovery_policy_grants_high_memory_one_non_stacking_extra_attempt(tmp_path):
    settings = Settings(
        workspace_root=tmp_path / "workspaces",
        recovery_memory_root=tmp_path / "legacy",
        recovery_base_retry_count=1,
        recovery_high_extra_retry_count=1,
        recovery_hard_max_attempts=2,
    )
    registry = RecoveryActionRegistry()
    policy = RecoveryPolicy(settings, registry)
    candidates = [
        {
            "action": "rerun_acceptance_with_same_inputs",
            "trust_level": "HIGH",
        },
        {
            "action": "rerun_acceptance_once",
            "trust_level": "HIGH",
        },
    ]

    assert policy.max_attempts(candidates) == 2
    assert [item.name for item in policy.executable_actions(candidates)] == [
        "rerun_acceptance_with_same_inputs",
        "rerun_acceptance_once",
    ]


@pytest.mark.asyncio
async def test_legacy_json_imports_only_successful_actions(recovery_context):
    store, advisor, _, session = recovery_context
    signature = build_signature(advisor)
    legacy_path = store.legacy_root / f"{store.repo_hash('/tmp/legacy')}.json"
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_text(
        json.dumps(
            {
                "signatures": {
                    signature.key: {
                        "signature": signature.to_dict(),
                        "actions": {
                            "rerun_acceptance_with_same_inputs": {
                                "success_count": 2,
                                "fail_count": 4,
                                "last_audit_id": "legacy-audit",
                                "avg_duration_ms": 80,
                            },
                            "rerun_acceptance_once": {
                                "success_count": 0,
                                "fail_count": 5,
                            },
                        },
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    actions = await store.list_project_actions(
        repo="/tmp/legacy",
        signature=signature,
    )

    assert [item["action"] for item in actions] == [
        "rerun_acceptance_with_same_inputs"
    ]
    assert actions[0]["success_count"] == 2
    assert legacy_path.with_suffix(".json.migrated").exists()
    rows = list((await session.execute(select(ProjectRecoveryMemory))).scalars())
    assert len(rows) == 1


def test_recovery_signature_separates_precise_failures(recovery_context):
    _, advisor, _, _ = recovery_context
    connection_refused = build_signature(
        advisor,
        category="DB_READY_ERROR",
        error_pattern="connection_refused",
    )
    auth_failed = build_signature(
        advisor,
        category="DB_READY_ERROR",
        error_pattern="db_auth_failed",
    )

    assert connection_refused.key != auth_failed.key
    assert connection_refused.match_key != auth_failed.match_key


def test_recovery_classification_includes_error_pattern(recovery_context):
    _, advisor, _, _ = recovery_context
    classification = advisor.classify_acceptance_failure(
        {"stdout": "", "stderr": "psycopg2: connection refused"}
    )

    assert classification.category == "DB_READY_ERROR"
    assert classification.error_pattern == "connection_refused"
