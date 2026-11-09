"""Audit task HTTP endpoints."""

from pathlib import Path
import asyncio
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from fastapi.responses import JSONResponse

from app.core.config import Settings, get_settings
from app.core.exceptions import AuditNotFoundError, InvalidInputError
from app.dependencies import get_audit_service, run_audit_task
from app.schemas.audit import (
    AuditArtifactContentData,
    AuditArtifactListData,
    AuditArtifactSummary,
    AuditCreateData,
    AuditCreateRequest,
    AuditListData,
    AuditListItem,
    AuditReportData,
    AuditStatusData,
    AuditStepSummary,
)
from app.schemas.common import ApiResponse
from app.services.audit_service import AuditService
from app.services.report_service import ReportService
from app.services.runtime_readiness import sandbox_readiness

router = APIRouter(prefix="/audits", tags=["audits"])
_admission_lock = asyncio.Lock()


@router.post("", response_model=ApiResponse[AuditCreateData])
async def create_audit(
    request: AuditCreateRequest,
    background_tasks: BackgroundTasks,
    service: Annotated[AuditService, Depends(get_audit_service)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ApiResponse[AuditCreateData]:
    """Create an audit task and optionally dispatch it in the background."""
    async with _admission_lock:
        if settings.app_env.lower() in {"production", "prod"}:
            readiness = await sandbox_readiness(settings)
            if not readiness["sandbox_available"]:
                return JSONResponse(status_code=503, content={
                    "code": "SANDBOX_UNAVAILABLE", "msg": readiness["sandbox_reason"], "data": None,
                })
            if not settings.llm_api_key or not settings.llm_model:
                return JSONResponse(status_code=503, content={
                    "code": "LLM_UNAVAILABLE", "msg": "审计模型尚未配置", "data": None,
                })
            pending = await service.audit_repo.count(status="queued") + await service.audit_repo.count(status="running")
            if pending >= settings.max_pending_audits:
                return JSONResponse(status_code=429, content={
                    "code": "QUEUE_FULL", "msg": "审计队列已满，请稍后再试", "data": None,
                })
        job = await service.create(request)
    if settings.enable_background_tasks:
        # The request is also persisted as an artifact by AuditService, so this
        # in-memory background task can be reconstructed after process restart.
        background_tasks.add_task(
            run_audit_task,
            job.id,
            request.model_dump(mode="json", exclude_unset=True),
        )
    else:
        await service.run(job.id, request)
    return ApiResponse(
        data=AuditCreateData(
            audit_id=job.id,
            status=job.status,
            created_at=job.created_at,
        )
    )


@router.get("", response_model=ApiResponse[AuditListData])
async def list_audits(
    service: Annotated[AuditService, Depends(get_audit_service)],
    requester_id: str | None = Query(default=None, min_length=1, max_length=255),
    status: str | None = Query(default=None, min_length=1, max_length=50),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> ApiResponse[AuditListData]:
    """List audit tasks for dashboards and manual polling tools."""
    jobs, total = await service.list(
        requester_id=requester_id,
        status=status,
        limit=limit,
        offset=offset,
    )
    return ApiResponse(
        data=AuditListData(
            total=total,
            limit=limit,
            offset=offset,
            items=[
                AuditListItem(
                    audit_id=job.id,
                    requester_id=job.requester_id,
                    audit_mode=job.audit_mode,
                    status=job.status,
                    risk_level=job.risk_level,
                    profile=job.profile,
                    created_at=job.created_at,
                    updated_at=job.updated_at,
                )
                for job in jobs
            ],
        )
    )


@router.get("/{audit_id}", response_model=ApiResponse[AuditStatusData])
async def get_audit(
    audit_id: str,
    service: Annotated[AuditService, Depends(get_audit_service)],
) -> ApiResponse[AuditStatusData]:
    """Return task status, summary, and per-step execution state."""
    job = await service.get(audit_id)
    return ApiResponse(
        data=AuditStatusData(
            audit_id=job.id,
            requester_id=job.requester_id,
            audit_mode=job.audit_mode,
            llm_policy=job.summary.get("llm_policy", "unknown"),
            status=job.status,
            profile=job.profile,
            summary=job.summary,
            steps=[
                AuditStepSummary(
                    step_id=step.id,
                    name=step.name,
                    command=step.command,
                    status=step.status,
                    exit_code=step.exit_code,
                    duration_ms=step.duration_ms,
                )
                for step in job.steps
            ],
            created_at=job.created_at,
            updated_at=job.updated_at,
        )
    )


@router.get(
    "/{audit_id}/artifacts",
    response_model=ApiResponse[AuditArtifactListData],
)
async def list_audit_artifacts(
    audit_id: str,
    service: Annotated[AuditService, Depends(get_audit_service)],
) -> ApiResponse[AuditArtifactListData]:
    """List files produced by an audit, such as reports and frozen test suites."""
    job = await service.get(audit_id)
    return ApiResponse(
        data=AuditArtifactListData(
            audit_id=job.id,
            artifacts=[
                AuditArtifactSummary(
                    artifact_type=artifact.artifact_type,
                    path=artifact.path,
                    metadata=artifact.metadata_json,
                    created_at=artifact.created_at,
                )
                for artifact in job.artifacts
            ],
        )
    )


@router.get(
    "/{audit_id}/artifacts/{artifact_type}",
    response_model=ApiResponse[AuditArtifactContentData],
)
async def get_audit_artifact_content(
    audit_id: str,
    artifact_type: str,
    service: Annotated[AuditService, Depends(get_audit_service)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ApiResponse[AuditArtifactContentData]:
    """Return the text content of one registered audit artifact."""
    job = await service.get(audit_id)
    artifact = next(
        (item for item in job.artifacts if item.artifact_type == artifact_type),
        None,
    )
    if artifact is None:
        raise AuditNotFoundError("audit artifact not found")
    path = Path(artifact.path).expanduser().resolve()
    # Only registered artifact paths are readable through this endpoint; callers
    # cannot pass arbitrary filesystem paths.
    root = (settings.workspace_root / audit_id).resolve()
    if not path.is_relative_to(root) or not path.exists() or not path.is_file():
        raise AuditNotFoundError("audit artifact file not found")
    if path.stat().st_size > 2 * 1024 * 1024:
        raise InvalidInputError("artifact content exceeds the 2 MiB preview limit")
    content = path.read_text(encoding="utf-8", errors="replace")
    return ApiResponse(
        data=AuditArtifactContentData(
            audit_id=job.id,
            artifact_type=artifact.artifact_type,
            path=str(path),
            content=content,
        )
    )


@router.get("/{audit_id}/report", response_model=ApiResponse[AuditReportData])
async def get_audit_report(
    audit_id: str,
    service: Annotated[AuditService, Depends(get_audit_service)],
) -> ApiResponse[AuditReportData]:
    """Build and return the current structured report for one audit."""
    job = await service.get(audit_id)
    report = ReportService().build(job)
    return ApiResponse(data=AuditReportData.model_validate(report))
