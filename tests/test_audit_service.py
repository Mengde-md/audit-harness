import json
import logging
import subprocess
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api_testing.schema_validator import ApiSchemaValidator
from app.api_testing.test_suite_freezer import TestSuiteFreezer
from app.core.config import Settings
from app.db.base import Base
from app.dependency_images.models import DependencyImageResult
from app.generators.acceptance_test_generator import SchemaAcceptanceTestGenerator
from app.models.audit_step import AuditStep
from app.models import audit_artifact, audit_job, audit_step  # noqa: F401
from app.parsers.api_document_parser import ApiDocumentParser
from app.parsers.basic_log_parser import BasicLogParser
from app.planners.check_planner import ProfileCheckPlanner
from app.runners.command_policy import CommandPolicy
from app.runners.image_policy import ImagePolicy
from app.runners.sandbox_runner import SandboxRunner
from app.schemas.api_schema import ApiEndpoint, ApiSchemaDocument
from app.schemas.audit import AuditCreateRequest
from app.services.api_test_service import ApiTestService
from app.services.audit_service import AuditService
from app.services.report_service import ReportService
from app.services.workspace_service import WorkspaceService
from app.utils.id_generator import generate_id
from app.utils.time import utc_now


class FakeParser(ApiDocumentParser):
    async def parse(self, markdown: str) -> ApiSchemaDocument:
        assert "GET /health" in markdown
        return ApiSchemaDocument(
            endpoints=[
                ApiEndpoint(
                    name="Health",
                    method="GET",
                    path="/health",
                    expected_status=200,
                    expected_json={"status": "ok"},
                )
            ]
        )


class FakeSandboxRunner(SandboxRunner):
    def __init__(self) -> None:
        self.acceptance_kwargs: dict[str, Any] = {}
        self.command_calls = 0

    async def run_command(self, **_: Any) -> dict[str, Any]:
        self.command_calls += 1
        return {
            "container_id": "container-check",
            "exit_code": 0,
            "stdout": "ok",
            "stderr": "",
            "duration_ms": 5,
            "timed_out": False,
        }

    async def run_acceptance(
        self,
        *,
        workspace_path: Path,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.acceptance_kwargs = kwargs
        result = {
            "total": 1,
            "passed": 1,
            "failed": 0,
            "tests": [
                {
                    "test_id": "test-1",
                    "name": "Health",
                    "passed": True,
                    "status_code": 200,
                    "duration_ms": 2,
                    "failures": [],
                    "response_excerpt": "{\"status\":\"ok\"}",
                }
            ],
        }
        (workspace_path / ".audit-acceptance-result.json").write_text(
            json.dumps(result)
        )
        return {
            "container_id": "container-api",
            "exit_code": 0,
            "stdout": json.dumps(result),
            "stderr": "",
            "duration_ms": 10,
            "timed_out": False,
        }


class FlakyAcceptanceRunner(FakeSandboxRunner):
    def __init__(self) -> None:
        super().__init__()
        self.acceptance_calls = 0

    async def run_acceptance(
        self,
        *,
        workspace_path: Path,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.acceptance_calls += 1
        self.acceptance_kwargs = kwargs
        if self.acceptance_calls == 1:
            return {
                "container_id": "container-api-first",
                "exit_code": 1,
                "stdout": "Waiting for application startup",
                "stderr": "service did not become ready before timeout",
                "duration_ms": 10,
                "timed_out": False,
            }
        return await super().run_acceptance(
            workspace_path=workspace_path,
            **kwargs,
        )


class FakeDependencyImageManager:
    def __init__(self) -> None:
        self.resolve_kwargs: dict[str, Any] = {}
        self.base_image: str | None = None

    async def resolve(self, *, base_image: str, **kwargs: Any) -> DependencyImageResult:
        self.resolve_kwargs = kwargs
        self.base_image = base_image
        return DependencyImageResult(
            image=f"{base_image}-with-deps",
            dependency_file="backend/requirements.txt",
            fingerprint="fake-fingerprint",
            cache_hit=False,
        )


def init_repo(repo: Path) -> str:
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "audit@example.com"],
        cwd=repo,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Audit Test"],
        cwd=repo,
        check=True,
    )
    (repo / "app.py").write_text("value = 1\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.mark.asyncio
async def test_full_audit_flow_with_fake_sandbox(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    repo = tmp_path / "repo"
    init_repo(repo)
    memory_root = repo / ".audit-harness"
    memory_root.mkdir()
    (memory_root / "PROJECT.md").write_text("# Test project\n", encoding="utf-8")
    (memory_root / "KNOWN_ISSUES.md").write_text(
        "# Known Issues\n\n## KI-001 Test issue\n",
        encoding="utf-8",
    )
    (memory_root / "project.json").write_text(
        json.dumps(
            {
                "version": 1,
                "dependency": {
                    "type": "python",
                    "manifest": "pyproject.toml",
                },
                "env_files": [".env"],
                "llm_adapters": [
                    {
                        "name": "embedding",
                        "type": "embedding",
                        "protocol": "openai",
                        "base_url_env": "MODEL_BASE_URL",
                        "api_key_env": "MODEL_API_KEY",
                        "model_env": "EMBEDDING_MODEL",
                    }
                ],
                "startup": {
                    "command": (
                        "uvicorn app.main:app --host 0.0.0.0 --port 8000"
                    ),
                    "port": 8000,
                },
                "services": {
                    "postgres": {
                        "enabled": True,
                        "variant": "pg16-vector",
                    },
                    "redis": {"enabled": True},
                },
                "setup_commands": ["cd backend && alembic upgrade head"],
            }
        ),
        encoding="utf-8",
    )
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        workspace_root=tmp_path / "workspaces",
        sandbox_allowed_images_raw="audit-harness/python-basic:3.11",
    )
    command_policy = CommandPolicy()
    sandbox_runner = FakeSandboxRunner()
    dependency_manager = FakeDependencyImageManager()

    async with session_factory() as session:
        service = AuditService(
            session=session,
            settings=settings,
            workspace_service=WorkspaceService(settings),
            dependency_image_manager=dependency_manager,
            api_test_service=ApiTestService(
                parser=FakeParser(),
                validator=ApiSchemaValidator(),
                generator=SchemaAcceptanceTestGenerator(),
                freezer=TestSuiteFreezer(),
            ),
            check_planner=ProfileCheckPlanner(command_policy),
            image_policy=ImagePolicy(settings),
            command_policy=command_policy,
            sandbox_runner=sandbox_runner,
            evidence_parser=BasicLogParser(),
            report_service=ReportService(),
        )
        request = AuditCreateRequest(
            requester_id="agent-test",
            repo=str(repo),
            audit_mode="full",
            llm_policy="mock",
            api_document_markdown="# Health\nGET /health",
            profile="python-basic",
        )
        job = await service.create(request)
        await service.run(job.id, request)
        finished = await service.get(job.id)

    assert finished.status == "passed"
    assert finished.audit_mode == "full"
    assert finished.sandbox_image.endswith("-with-deps")
    assert finished.summary["dependency_image"]["cache_hit"] is False
    assert finished.test_suite_hash.startswith("sha256:")
    assert finished.summary["api_validation"]["passed_tests"] == 1
    assert finished.summary["llm_mock_profile"] == "generic-agent"
    assert finished.startup_command == "uvicorn app.main:app --host 0.0.0.0 --port 8000"
    assert finished.service_port == 8000
    assert finished.summary["dependency_config"]["manifest"] == "pyproject.toml"
    assert finished.summary["runtime"]["language"] == "python"
    assert finished.summary["runtime"]["version"] == "3.11"
    assert finished.summary["runtime"]["image"] == finished.sandbox_image
    assert finished.summary["env_files"] == [".env"]
    assert finished.summary["llm_adapters"][0]["base_url_env"] == "MODEL_BASE_URL"
    assert finished.summary["services"]["postgres"]["variant"] == "pg16-vector"
    assert finished.summary["services"]["redis"]["enabled"] is True
    assert finished.summary["setup_command_count"] == 1
    assert dependency_manager.resolve_kwargs["manifest"] == "pyproject.toml"
    assert dependency_manager.base_image == "audit-harness/python-basic:3.11"
    assert sandbox_runner.acceptance_kwargs["services"]["postgres"]["enabled"] is True
    assert sandbox_runner.acceptance_kwargs["env_files"] == [".env"]
    assert sandbox_runner.acceptance_kwargs["llm_adapters"][0]["type"] == "embedding"
    assert sandbox_runner.acceptance_kwargs["setup_commands"] == [
        "cd backend && alembic upgrade head"
    ]
    request_artifact = next(
        item for item in finished.artifacts
        if item.artifact_type == "audit_request_json"
    )
    assert json.loads(Path(request_artifact.path).read_text())["requester_id"] == (
        "agent-test"
    )
    assert finished.summary["project_memory"]["known_issue_count"] == 1
    assert "PROJECT.md" in finished.summary["project_memory"]["loaded_files"]
    mock_rules_artifact = next(
        item for item in finished.artifacts
        if item.artifact_type == "llm_mock_rules_json"
    )
    mock_config = json.loads(Path(mock_rules_artifact.path).read_text())
    assert mock_config["profile"] == "generic-agent"
    assert mock_config["rules"] == []
    assert len(finished.steps) == 3
    assert (settings.workspace_root / job.id / "report.json").exists()
    assert (settings.workspace_root / job.id / "report.md").exists()
    assert (settings.workspace_root / job.id / "execution-events.jsonl").exists()
    await engine.dispose()


@pytest.mark.asyncio
async def test_project_config_can_select_python_runtime_version(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    repo = tmp_path / "repo"
    init_repo(repo)
    config_root = repo / ".audit-harness"
    config_root.mkdir()
    (config_root / "project.json").write_text(
        json.dumps(
            {
                "version": 1,
                "runtime": {
                    "language": "python",
                    "version": "3.12",
                },
                "startup": {
                    "command": (
                        "uvicorn app.main:app --host 0.0.0.0 --port 8000"
                    ),
                    "port": 8000,
                },
                "checks": [
                    {
                        "name": "python_compile",
                        "command": "python -m compileall -q .",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        workspace_root=tmp_path / "workspaces",
        sandbox_allowed_images_raw=(
            "audit-harness/python-basic:3.11,"
            "audit-harness/python-basic:3.12"
        ),
    )
    command_policy = CommandPolicy()
    dependency_manager = FakeDependencyImageManager()

    async with session_factory() as session:
        service = AuditService(
            session=session,
            settings=settings,
            workspace_service=WorkspaceService(settings),
            dependency_image_manager=dependency_manager,
            api_test_service=ApiTestService(
                parser=FakeParser(),
                validator=ApiSchemaValidator(),
                generator=SchemaAcceptanceTestGenerator(),
                freezer=TestSuiteFreezer(),
            ),
            check_planner=ProfileCheckPlanner(command_policy),
            image_policy=ImagePolicy(settings),
            command_policy=command_policy,
            sandbox_runner=FakeSandboxRunner(),
            evidence_parser=BasicLogParser(),
            report_service=ReportService(),
        )
        request = AuditCreateRequest(
            requester_id="agent-test",
            repo=str(repo),
            audit_mode="full",
            llm_policy="mock",
            api_document_markdown="# Health\nGET /health",
            profile="python-basic",
        )
        job = await service.create(request)
        await service.run(job.id, request)
        finished = await service.get(job.id)

    assert finished.status == "passed"
    assert dependency_manager.base_image == "audit-harness/python-basic:3.12"
    assert finished.summary["runtime"]["version"] == "3.12"
    assert finished.summary["runtime"]["image"] == (
        "audit-harness/python-basic:3.12-with-deps"
    )
    report_path = settings.workspace_root / job.id / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["runtime"]["version"] == "3.12"
    await engine.dispose()


@pytest.mark.asyncio
async def test_resume_skips_previously_passed_steps(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    repo = tmp_path / "repo"
    init_repo(repo)
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        workspace_root=tmp_path / "workspaces",
        sandbox_allowed_images_raw="audit-harness/python-basic:3.11",
    )
    command_policy = CommandPolicy()
    sandbox_runner = FakeSandboxRunner()

    async with session_factory() as session:
        service = AuditService(
            session=session,
            settings=settings,
            workspace_service=WorkspaceService(settings),
            dependency_image_manager=FakeDependencyImageManager(),
            api_test_service=ApiTestService(
                parser=FakeParser(),
                validator=ApiSchemaValidator(),
                generator=SchemaAcceptanceTestGenerator(),
                freezer=TestSuiteFreezer(),
            ),
            check_planner=ProfileCheckPlanner(command_policy),
            image_policy=ImagePolicy(settings),
            command_policy=command_policy,
            sandbox_runner=sandbox_runner,
            evidence_parser=BasicLogParser(),
            report_service=ReportService(),
        )
        request = AuditCreateRequest(
            requester_id="agent-test",
            repo=str(repo),
            audit_mode="full",
            llm_policy="mock",
            api_document_markdown="# Health\nGET /health",
            startup_command="uvicorn app.main:app --host 0.0.0.0 --port 8000",
            service_port=8000,
            profile="python-basic",
        )
        job = await service.create(request)
        await service.run(job.id, request)
        first_command_calls = sandbox_runner.command_calls
        await service.run(job.id, request)
        finished = await service.get(job.id)

    assert finished.status == "passed"
    assert sandbox_runner.command_calls == first_command_calls
    assert finished.summary["resume"]["skipped_steps"]
    assert finished.summary["recovery"]["decision"]["resumed"] is True
    await engine.dispose()


@pytest.mark.asyncio
async def test_resume_marks_running_steps_interrupted(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    repo = tmp_path / "repo"
    init_repo(repo)
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        workspace_root=tmp_path / "workspaces",
        sandbox_allowed_images_raw="audit-harness/python-basic:3.11",
    )
    command_policy = CommandPolicy()

    async with session_factory() as session:
        service = AuditService(
            session=session,
            settings=settings,
            workspace_service=WorkspaceService(settings),
            dependency_image_manager=FakeDependencyImageManager(),
            api_test_service=ApiTestService(
                parser=FakeParser(),
                validator=ApiSchemaValidator(),
                generator=SchemaAcceptanceTestGenerator(),
                freezer=TestSuiteFreezer(),
            ),
            check_planner=ProfileCheckPlanner(command_policy),
            image_policy=ImagePolicy(settings),
            command_policy=command_policy,
            sandbox_runner=FakeSandboxRunner(),
            evidence_parser=BasicLogParser(),
            report_service=ReportService(),
        )
        request = AuditCreateRequest(
            requester_id="agent-test",
            repo=str(repo),
            audit_mode="full",
            llm_policy="mock",
            api_document_markdown="# Health\nGET /health",
            startup_command="uvicorn app.main:app --host 0.0.0.0 --port 8000",
            service_port=8000,
            profile="python-basic",
        )
        job = await service.create(request)
        job.status = "running"
        await service.audit_repo.save(job)
        await service.step_repo.create(
            AuditStep(
                id=generate_id(),
                audit_id=job.id,
                name="python_compile",
                command="python -m compileall -q backend",
                image=job.sandbox_image,
                status="running",
                findings=[],
                metadata_json={},
                started_at=utc_now(),
            )
        )
        await service.run(job.id, request)
        finished = await service.get(job.id)

    interrupted = [step for step in finished.steps if step.status == "interrupted"]
    assert interrupted
    assert finished.summary["resume"]["resumed"] is True
    assert finished.summary["resume"]["interrupted_steps"][0]["name"] == (
        "python_compile"
    )
    await engine.dispose()


@pytest.mark.asyncio
async def test_acceptance_recovery_retries_transient_startup_failure(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    repo = tmp_path / "repo"
    init_repo(repo)
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        workspace_root=tmp_path / "workspaces",
        recovery_memory_root=tmp_path / "recovery",
        sandbox_allowed_images_raw="audit-harness/python-basic:3.11",
    )
    command_policy = CommandPolicy()
    sandbox_runner = FlakyAcceptanceRunner()

    async with session_factory() as session:
        service = AuditService(
            session=session,
            settings=settings,
            workspace_service=WorkspaceService(settings),
            dependency_image_manager=FakeDependencyImageManager(),
            api_test_service=ApiTestService(
                parser=FakeParser(),
                validator=ApiSchemaValidator(),
                generator=SchemaAcceptanceTestGenerator(),
                freezer=TestSuiteFreezer(),
            ),
            check_planner=ProfileCheckPlanner(command_policy),
            image_policy=ImagePolicy(settings),
            command_policy=command_policy,
            sandbox_runner=sandbox_runner,
            evidence_parser=BasicLogParser(),
            report_service=ReportService(),
        )
        request = AuditCreateRequest(
            requester_id="agent-test",
            repo=str(repo),
            audit_mode="full",
            llm_policy="mock",
            api_document_markdown="# Health\nGET /health",
            startup_command="uvicorn app.main:app --host 0.0.0.0 --port 8000",
            service_port=8000,
            profile="python-basic",
        )
        job = await service.create(request)
        await service.run(job.id, request)
        finished = await service.get(job.id)

    assert finished.status == "passed"
    assert sandbox_runner.acceptance_calls == 2
    assert finished.summary["recovery"]["decision"]["retry"] is True
    assert finished.summary["recovery"]["attempts"][0]["result"] == "passed"
    assert [step.name for step in finished.steps][-2:] == [
        "api_acceptance",
        "api_acceptance_retry_1",
    ]
    report_path = settings.workspace_root / job.id / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["quality"]["failure_attribution"]["category"] == "NONE"
    await engine.dispose()


@pytest.mark.asyncio
async def test_missing_default_project_config_warns_and_falls_back(
    tmp_path,
    caplog,
):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    repo = tmp_path / "repo"
    init_repo(repo)
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        workspace_root=tmp_path / "workspaces",
        sandbox_allowed_images_raw="audit-harness/python-basic:3.11",
    )
    command_policy = CommandPolicy()

    async with session_factory() as session:
        service = AuditService(
            session=session,
            settings=settings,
            workspace_service=WorkspaceService(settings),
            dependency_image_manager=FakeDependencyImageManager(),
            api_test_service=ApiTestService(
                parser=FakeParser(),
                validator=ApiSchemaValidator(),
                generator=SchemaAcceptanceTestGenerator(),
                freezer=TestSuiteFreezer(),
            ),
            check_planner=ProfileCheckPlanner(command_policy),
            image_policy=ImagePolicy(settings),
            command_policy=command_policy,
            sandbox_runner=FakeSandboxRunner(),
            evidence_parser=BasicLogParser(),
            report_service=ReportService(),
        )
        request = AuditCreateRequest(
            requester_id="agent-test",
            repo=str(repo),
            audit_mode="full",
            llm_policy="mock",
            api_document_markdown="# Health\nGET /health",
            startup_command="uvicorn app.main:app --host 0.0.0.0 --port 8000",
            service_port=8000,
            profile="python-basic",
        )
        with caplog.at_level(logging.WARNING, logger="audit_harness.audit"):
            job = await service.create(request)

    assert "PROJECT_CONFIG_MISSING" in caplog.text
    assert job.summary["project_config_warnings"] == [
        "default project_config not found: .audit-harness/project.json; "
        "falling back to request fields and auto detection"
    ]
    await engine.dispose()
