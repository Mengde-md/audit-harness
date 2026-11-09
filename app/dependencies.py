"""FastAPI dependency wiring and background task recovery helpers."""

import asyncio
import json
import logging
from pathlib import Path
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api_testing.schema_validator import ApiSchemaValidator
from app.api_testing.test_suite_freezer import TestSuiteFreezer
from app.core.config import Settings, get_settings
from app.db.session import async_session_factory
from app.dependency_images.builder import DependencyImageManager
from app.generators.acceptance_test_generator import SchemaAcceptanceTestGenerator
from app.memory.test_suite_cache import LlmTestSuiteCache
from app.parsers.api_document_parser import LlmApiDocumentParser
from app.parsers.basic_log_parser import BasicLogParser
from app.planners.check_planner import ProfileCheckPlanner
from app.providers.llm.openai_compatible import OpenAICompatibleLlmProvider
from app.runners.command_policy import CommandPolicy
from app.runners.docker_runner import DockerSandboxRunner
from app.runners.image_policy import ImagePolicy
from app.services.api_test_service import ApiTestService
from app.services.audit_service import AuditService
from app.services.code_review_service import CodeReviewService
from app.services.report_service import ReportService
from app.services.workspace_service import WorkspaceService

_audit_slot = asyncio.Semaphore(1)


async def get_session():
    """Provide one async SQLAlchemy session per request."""
    async with async_session_factory() as session:
        yield session


def build_audit_service(session: AsyncSession, settings: Settings) -> AuditService:
    """Assemble the production AuditService dependency graph."""
    command_policy = CommandPolicy()
    llm_provider = OpenAICompatibleLlmProvider(settings)
    api_test_service = ApiTestService(
        parser=LlmApiDocumentParser(llm_provider),
        validator=ApiSchemaValidator(),
        generator=SchemaAcceptanceTestGenerator(),
        freezer=TestSuiteFreezer(),
        cache=LlmTestSuiteCache(settings.test_suite_cache_root),
    )
    return AuditService(
        session=session,
        settings=settings,
        workspace_service=WorkspaceService(settings),
        dependency_image_manager=DependencyImageManager(settings),
        api_test_service=api_test_service,
        check_planner=ProfileCheckPlanner(command_policy),
        image_policy=ImagePolicy(settings),
        command_policy=command_policy,
        sandbox_runner=DockerSandboxRunner(settings),
        evidence_parser=BasicLogParser(),
        report_service=ReportService(),
        code_review_service=CodeReviewService(
            settings=settings,
            llm_provider=llm_provider,
        ),
    )


def get_audit_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AuditService:
    """FastAPI dependency wrapper for AuditService."""
    return build_audit_service(session, settings)


async def run_audit_task(audit_id: str, request_data: dict) -> None:
    """Run a background audit using a fresh DB session."""
    from app.schemas.audit import AuditCreateRequest

    # Older request artifacts stored every field, including these defaults.
    # Only normalize complete legacy snapshots that have no actual mock config.
    legacy_mock_defaults = {
        "llm_mock_profile": "generic-agent",
        "llm_mock_rules": [],
        "llm_mock_fallback": None,
    }
    if (
        isinstance(request_data, dict)
        and request_data.get("llm_policy") == "disabled"
        and AuditCreateRequest.model_fields.keys() <= request_data.keys()
        and all(request_data[key] == value for key, value in legacy_mock_defaults.items())
    ):
        request_data = {
            key: value for key, value in request_data.items() if key not in legacy_mock_defaults
        }
    request = AuditCreateRequest.model_validate(request_data)
    async with _audit_slot:
        async with async_session_factory() as session:
            service = build_audit_service(session, get_settings())
            await service.run(audit_id, request)


async def recover_incomplete_audit_tasks() -> None:
    """Schedule unfinished audits after application startup.

    BackgroundTasks are in-memory, so a process restart loses the callable. The
    audit request artifact is the durable source used to reconstruct the task.
    """
    logger = logging.getLogger("audit_harness.audit")
    async with async_session_factory() as session:
        service = build_audit_service(session, get_settings())
        jobs = await service.list_recoverable()
    for job in jobs:
        # Jobs without the original request are unsafe to replay because request
        # fields like mock rules and setup commands may not be reconstructable.
        artifact = next(
            (
                item
                for item in job.artifacts
                if item.artifact_type == "audit_request_json"
            ),
            None,
        )
        if artifact is None:
            logger.warning(
                "AUDIT_RECOVERY_SKIP | audit_id=%s | reason=missing_request_artifact",
                job.id,
            )
            continue
        try:
            request_data = json.loads(Path(artifact.path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(
                "AUDIT_RECOVERY_SKIP | audit_id=%s | reason=invalid_request_artifact "
                "| error=%s",
                job.id,
                exc,
            )
            continue
        logger.info(
            "AUDIT_RECOVERY_SCHEDULE | audit_id=%s | previous_status=%s",
            job.id,
            job.status,
        )
        # Fire-and-forget is deliberate here: startup should not block on long
        # audits, and each recovered task owns its DB session.
        asyncio.create_task(run_audit_task(job.id, request_data))
