import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import BackgroundTasks

from app import dependencies
from app.api.v1.routes.audits import create_audit
from app.core.config import Settings
from app.schemas.audit import AuditCreateRequest
from app.utils.time import utc_now


def _request() -> AuditCreateRequest:
    return AuditCreateRequest(
        requester_id="site",
        repo="https://github.com/example/test.git",
        audit_mode="full",
        llm_policy="disabled",
        api_document_markdown="# Health\nGET /health",
        env_files=[],
    )


def _service_for_background(monkeypatch, tmp_path):
    settings = Settings(app_env="local", workspace_root=tmp_path, enable_background_tasks=True)
    service = dependencies.build_audit_service(AsyncMock(), settings)
    monkeypatch.setattr(dependencies, "async_session_factory", AsyncMock)
    monkeypatch.setattr(dependencies, "build_audit_service", lambda *_: service)
    return service, settings


async def test_disabled_policy_dispatch_and_artifact_preserve_fields(monkeypatch, tmp_path):
    service, settings = _service_for_background(monkeypatch, tmp_path)
    service._create_artifact = AsyncMock()
    service.run = AsyncMock()

    async def create(request):
        await service._write_request_artifact("job", request)
        return SimpleNamespace(id="job", status="queued", created_at=utc_now())

    service.create = create
    request = _request()
    tasks = BackgroundTasks()
    await create_audit(request, tasks, service, settings)
    await tasks()

    audit_id, dispatched = service.run.await_args.args
    assert audit_id == "job"
    assert dispatched == request
    assert dispatched.model_fields_set == request.model_fields_set
    assert "env_files" in dispatched.model_fields_set  # Explicit empty value is retained.
    assert "project_config" not in dispatched.model_fields_set
    saved = json.loads((tmp_path / "job" / "audit_request.json").read_text(encoding="utf-8"))
    assert AuditCreateRequest.model_validate(saved).model_fields_set == request.model_fields_set


async def test_restart_recovers_disabled_policy_from_legacy_artifact(monkeypatch, tmp_path):
    service, _ = _service_for_background(monkeypatch, tmp_path)
    path = tmp_path / "audit_request.json"
    original = _request()
    legacy = original.model_dump(mode="json")
    path.write_text(json.dumps(legacy), encoding="utf-8")
    job = SimpleNamespace(
        id="legacy-job",
        status="queued",
        artifacts=[SimpleNamespace(artifact_type="audit_request_json", path=str(path))],
    )
    service.list_recoverable = AsyncMock(return_value=[job])
    recovered = asyncio.Event()

    async def run(audit_id, request):
        recovered.set()

    service.run = AsyncMock(side_effect=run)
    await dependencies.recover_incomplete_audit_tasks()
    await asyncio.wait_for(recovered.wait(), timeout=1)
    audit_id, restored = service.run.await_args.args
    assert audit_id == job.id
    assert restored == original
    assert all(not field.startswith("llm_mock_") for field in restored.model_fields_set)
