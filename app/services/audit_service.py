"""Audit orchestration service.

This module owns the end-to-end audit lifecycle: creating jobs, preparing the
workspace, generating API tests, executing sandbox checks, collecting artifacts,
and producing the final JSON/Markdown reports.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.error_codes import ErrorCode
from app.core.exceptions import (
    ApiDocumentError,
    AuditHarnessError,
    AuditNotFoundError,
    InvalidInputError,
    SandboxError,
)
from app.dependency_images.builder import DependencyImageManager
from app.memory.models import ProjectMemory
from app.memory.project_memory import ProjectMemoryLoader
from app.memory.recovery_memory import RecoveryAdvisor, RecoveryMemoryStore
from app.memory.task_memory import TaskMemoryWriter
from app.models.audit_artifact import AuditArtifact
from app.models.audit_job import AuditJob
from app.models.audit_step import AuditStep
from app.parsers.evidence_parser import EvidenceParser
from app.planners.check_planner import CheckPlanner
from app.repositories.artifact_repository import ArtifactRepository
from app.repositories.audit_repository import AuditRepository
from app.repositories.step_repository import StepRepository
from app.runners.command_policy import CommandPolicy
from app.runners.image_policy import ImagePolicy
from app.runners.sandbox_runner import SandboxRunner
from app.schemas.audit import AuditCreateRequest
from app.schemas.audit_summary import (
    ApiValidationSummary,
    AuditSummary,
    DependencyImageSummary,
    ProjectMemorySummary,
    RuntimeSummary,
)
from app.services.acceptance_execution_service import AcceptanceExecutionService
from app.services.api_test_service import ApiTestService
from app.services.code_review_service import CodeReviewService
from app.services.project_config_resolver import ProjectConfigResolver
from app.services.report_service import ReportService
from app.services.workspace_service import WorkspaceService
from app.utils.id_generator import generate_id
from app.utils.process import sha256_text, truncate_tail
from app.utils.time import utc_now


class AuditService:
    """Coordinate one audit job across persistence, sandbox execution, and reports."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        settings: Settings,
        workspace_service: WorkspaceService,
        dependency_image_manager: DependencyImageManager,
        api_test_service: ApiTestService,
        check_planner: CheckPlanner,
        image_policy: ImagePolicy,
        command_policy: CommandPolicy,
        sandbox_runner: SandboxRunner,
        evidence_parser: EvidenceParser,
        report_service: ReportService,
        code_review_service: CodeReviewService | None = None,
    ):
        self.session = session
        self.settings = settings
        self.workspace_service = workspace_service
        self.dependency_image_manager = dependency_image_manager
        self.api_test_service = api_test_service
        self.check_planner = check_planner
        self.image_policy = image_policy
        self.command_policy = command_policy
        self.sandbox_runner = sandbox_runner
        self.evidence_parser = evidence_parser
        self.report_service = report_service
        self.code_review_service = code_review_service or CodeReviewService(
            settings=settings,
        )
        self.audit_repo = AuditRepository(session)
        self.step_repo = StepRepository(session)
        self.artifact_repo = ArtifactRepository(session)
        self.project_memory_loader = ProjectMemoryLoader()
        self.project_config_resolver = ProjectConfigResolver()
        self.acceptance_execution = AcceptanceExecutionService(
            settings=settings,
            step_repo=self.step_repo,
            sandbox_runner=sandbox_runner,
            evidence_parser=evidence_parser,
            recovery_advisor=RecoveryAdvisor(
                RecoveryMemoryStore(session, settings)
            ),
        )

    async def create(self, request: AuditCreateRequest) -> AuditJob:
        """Create a queued audit job and persist the original request for recovery."""
        self.workspace_service.validate_repo(request.repo)
        self.project_config_resolver.apply(request)
        if not request.startup_command or request.service_port is None:
            raise InvalidInputError(
                "startup command and service_port are required, either in request "
                "or project_config"
            )
        self.command_policy.validate(request.startup_command)
        for command in request.setup_commands:
            self.command_policy.validate(command)
        image = self.image_policy.for_profile(
            request.profile,
            request.runtime.version,
        )
        now = utc_now()
        job = AuditJob(
            id=generate_id(),
            requester_id=request.requester_id,
            repo=request.repo,
            audit_mode=request.audit_mode,
            base_commit=request.base_commit,
            patch_hash=sha256_text(request.patch) if request.patch else None,
            api_document_hash=sha256_text(request.api_document_markdown),
            startup_command=request.startup_command,
            service_port=request.service_port,
            profile=request.profile,
            sandbox_backend=self.settings.sandbox_backend,
            sandbox_image=image,
            status="queued",
            summary=AuditSummary(
                llm_policy=request.llm_policy,
                project_config_warnings=request.project_config_warnings,
                runtime=RuntimeSummary(
                    language=request.runtime.language,
                    version=request.runtime.version,
                    image=image,
                    source=(
                        "request"
                        if "runtime" in request.model_fields_set
                        else "project_config_or_default"
                    ),
                ),
            ).to_json_dict(),
            risk_level="unknown",
            created_at=now,
            updated_at=now,
        )
        created = await self.audit_repo.create(job)
        logging.getLogger("audit_harness.audit").info(
            "AUDIT_CREATED | audit_id=%s | requester_id=%s | mode=%s "
            "| profile=%s | llm_policy=%s | repo=%s",
            created.id,
            created.requester_id,
            created.audit_mode,
            created.profile,
            request.llm_policy,
            created.repo,
        )
        await self._write_request_artifact(created.id, request)
        return created

    async def get(self, audit_id: str) -> AuditJob:
        """Load one audit job with its steps/artifacts or raise a business 404."""
        job = await self.audit_repo.get(audit_id)
        if job is None:
            raise AuditNotFoundError("audit job not found")
        return job

    async def list(
        self,
        *,
        requester_id: str | None = None,
        status: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[AuditJob], int]:
        """Return a paged task list plus the matching total for external consoles."""
        jobs = await self.audit_repo.list(
            requester_id=requester_id,
            status=status,
            limit=limit,
            offset=offset,
        )
        total = await self.audit_repo.count(
            requester_id=requester_id,
            status=status,
        )
        return jobs, total

    async def list_recoverable(self) -> list[AuditJob]:
        """Find tasks that should be resumed after service restart."""
        return await self.audit_repo.list_by_statuses(["queued", "running", "retrying"])

    async def run(self, audit_id: str, request: AuditCreateRequest) -> None:
        """Execute or resume an audit job.

        The method is intentionally idempotent for the same ``audit_id`` where
        possible: prepared workspaces are reused, passed check steps are skipped,
        and already-passed acceptance steps are not repeated.
        """
        self.project_config_resolver.apply(request)
        if not request.startup_command or request.service_port is None:
            raise InvalidInputError(
                "startup command and service_port are required, either in request "
                "or project_config"
            )
        logger = logging.getLogger("audit_harness.audit")
        logger.info(
            "AUDIT_START | audit_id=%s | mode=%s | profile=%s",
            audit_id,
            request.audit_mode,
            request.profile,
        )
        job = await self.get(audit_id)
        resume_summary = await self._prepare_resume(job)
        # Mark the job running only after resume cleanup so interrupted steps are
        # preserved as evidence rather than silently overwritten.
        job.status = "running"
        job.updated_at = utc_now()
        await self.audit_repo.save(job)

        try:
            workspace, audit_input = await self.workspace_service.prepare(
                audit_id=audit_id,
                repo=request.repo,
                audit_mode=request.audit_mode,
                base_commit=request.base_commit,
                patch=request.patch,
            )
            self.project_config_resolver.apply(request, repo_path=workspace)
            self.command_policy.validate(request.startup_command)
            for command in request.setup_commands:
                self.command_policy.validate(command)
            job.sandbox_image = self.image_policy.for_profile(request.profile, request.runtime.version)
            task_memory = TaskMemoryWriter(workspace.parent)
            task_memory.append(
                "workspace_prepared",
                audit_id=audit_id,
                workspace=str(workspace),
            )
            project_memory = self.project_memory_loader.load(workspace)
            task_memory.append(
                "project_memory_loaded",
                files=project_memory.loaded_files,
                warnings=project_memory.warnings,
            )
            job.workspace_path = str(workspace)
            await self._write_input_artifacts(
                job,
                request=request,
                workspace=workspace,
                audit_input=audit_input,
                project_memory=project_memory,
            )

            dependency_image = await self.dependency_image_manager.resolve(
                workspace=workspace,
                profile=request.profile,
                base_image=job.sandbox_image,
                manifest=request.dependency.manifest if request.dependency else None,
            )
            # Dependency resolution can promote the base sandbox image to a
            # project-specific cached image that already contains Python deps.
            job.sandbox_image = dependency_image.image
            summary = AuditSummary.model_validate(job.summary or {})
            summary.dependency_image = DependencyImageSummary(
                image=dependency_image.image,
                dependency_file=dependency_image.dependency_file,
                fingerprint=dependency_image.fingerprint or "",
                cache_hit=dependency_image.cache_hit,
            )
            summary.runtime = RuntimeSummary(
                language=request.runtime.language,
                version=request.runtime.version,
                image=job.sandbox_image,
                source=(
                    "request"
                    if "runtime" in request.model_fields_set
                    else "project_config_or_default"
                ),
            )
            summary.llm_policy = request.llm_policy
            summary.llm_mock_profile = request.llm_mock_profile
            summary.llm_mock_rule_count = len(request.llm_mock_rules)
            summary.project_config_warnings = request.project_config_warnings
            summary.dependency_config = (
                request.dependency.model_dump(mode="json")
                if request.dependency
                else None
            )
            summary.env_files = request.env_files
            summary.llm_adapters = [
                adapter.model_dump(mode="json")
                for adapter in request.llm_adapters
            ]
            summary.services = request.services.model_dump(mode="json")
            summary.setup_command_count = len(request.setup_commands)
            summary.project_memory = ProjectMemorySummary(
                loaded_files=project_memory.loaded_files,
                known_issue_count=project_memory.known_issue_count,
                warnings=project_memory.warnings,
            )
            job.summary = summary.to_json_dict()
            await self.audit_repo.save(job)

            schema, suite, schema_path, suite_path, suite_hash, suite_memory = (
                await self.api_test_service.prepare(
                    markdown=request.api_document_markdown,
                    input_dir=audit_input,
                    profile=request.profile,
                    model=self.settings.llm_model,
                    project_suite=project_memory.acceptance_suite,
                )
            )
            # Freezing schema and test suite makes every audit reproducible: the
            # report can point to exactly what was executed in the sandbox.
            task_memory.append(
                "test_suite_prepared",
                source=suite_memory["source"],
                cache_key=suite_memory["cache_key"],
                test_count=len(suite.tests),
            )
            job.api_schema_id = generate_id()
            job.test_suite_id = suite.id
            job.test_suite_hash = suite_hash
            summary = AuditSummary.model_validate(job.summary or {})
            summary.api_validation = ApiValidationSummary(
                schema_id=job.api_schema_id,
                test_suite_id=suite.id,
                test_suite_hash=suite_hash,
                generated_tests=len(suite.tests),
                warnings=schema.warnings,
                missing_fields=schema.missing_fields,
                test_suite_source=suite_memory["source"],
                cache_key=suite_memory["cache_key"],
                cache_hit=suite_memory["cache_hit"],
            )
            job.summary = summary.to_json_dict()
            await self.audit_repo.save(job)
            await self._create_artifact(job.id, "api_schema_json", schema_path)
            await self._create_artifact(job.id, "acceptance_suite_json", suite_path)

            checks = (
                [item.model_dump() for item in request.checks]
                if request.checks
                else None
            )
            plan = self.check_planner.build_plan(request.profile, checks)
            failed = False
            for check in plan:
                skipped_step = self._passed_step(job, check["name"], check["command"])
                if skipped_step is not None:
                    # Resume path: successful deterministic checks do not need to
                    # consume another sandbox run after a process restart.
                    task_memory.append(
                        "step_skipped",
                        name=check["name"],
                        step_id=skipped_step.id,
                        reason="previous step already passed",
                    )
                    resume_summary["skipped_steps"].append(
                        {
                            "step_id": skipped_step.id,
                            "name": skipped_step.name,
                            "reason": "previous step already passed",
                        }
                    )
                    continue
                logger.info(
                    "AUDIT_STEP_START | audit_id=%s | step=%s",
                    audit_id,
                    check["name"],
                )
                result = await self._execute_step(
                    job=job,
                    name=check["name"],
                    command=check["command"],
                    workspace=workspace,
                )
                task_memory.append(
                    "step_completed",
                    name=check["name"],
                    status=result.status,
                    exit_code=result.exit_code,
                )
                failed = failed or result.exit_code != 0

            # Project checks may have created links or oversized files. Validate
            # again before any host-side scanner reads their output.
            await asyncio.to_thread(self.workspace_service._validate_tree, workspace)
            review_result, review_json_path, review_markdown_path = (
                await self.code_review_service.review(
                    workspace=workspace,
                    audit_root=workspace.parent,
                    request=request,
                )
            )
            task_memory.append(
                "code_review_completed",
                finding_count=review_result.counts["total"],
                high=review_result.counts["high"],
                medium=review_result.counts["medium"],
                low=review_result.counts["low"],
                llm_enabled=review_result.llm_review.enabled,
            )
            summary = AuditSummary.model_validate(job.summary or {})
            summary.code_review = review_result.to_summary()
            job.summary = summary.to_json_dict()
            await self.audit_repo.save(job)
            await self._create_artifact(
                job.id,
                "code_review_result_json",
                review_json_path,
            )
            await self._create_artifact(
                job.id,
                "code_review_report_markdown",
                review_markdown_path,
            )

            acceptance, recovery_summary = await self.acceptance_execution.execute(
                job=job,
                request=request,
                workspace=workspace,
                audit_input=audit_input,
                task_memory=task_memory,
            )
            # The sandbox runner writes the detailed per-API result into the
            # workspace; the service reads it back into the persistent summary.
            acceptance_result_path = workspace / ".audit-acceptance-result.json"
            acceptance_summary = self._read_acceptance_result(acceptance_result_path)
            task_memory.append(
                "acceptance_completed",
                status=acceptance.status,
                passed=acceptance_summary.get("passed", 0),
                failed=acceptance_summary.get("failed", 0),
            )
            failed = failed or acceptance.exit_code != 0
            summary = AuditSummary.model_validate(job.summary or {})
            summary.api_validation.passed_tests = acceptance_summary.get("passed", 0)
            summary.api_validation.failed_tests = acceptance_summary.get(
                "failed",
                len(suite.tests),
            )
            summary.api_validation.results = acceptance_summary.get("tests", [])
            fresh_job = await self.get(job.id)
            summary.passed = not failed
            summary.total_steps = len(fresh_job.steps)
            summary.passed_steps = sum(
                step.status == "passed" for step in fresh_job.steps
            )
            summary.failed_steps = sum(
                step.status in {"failed", "timeout"} for step in fresh_job.steps
            )
            summary.recovery = recovery_summary
            summary.resume = resume_summary
            job.summary = summary.to_json_dict()
            if acceptance_result_path.exists():
                await self._create_artifact(
                    job.id,
                    "acceptance_result_json",
                    acceptance_result_path,
                )
            job.status = "failed" if failed else "passed"
            job.risk_level = "high" if failed else "low"
        except Exception as exc:
            logger.exception(
                "AUDIT_ERROR | audit_id=%s | error_type=%s",
                audit_id,
                type(exc).__name__,
            )
            await self.session.rollback()
            job = await self.get(audit_id)
            job.status = "error"
            job.risk_level = "high"
            job.error_type = self._error_type(exc)
            if isinstance(exc, AuditHarnessError):
                job.error_message = exc.public_message
                error_code = exc.code
            else:
                job.error_message = ErrorCode.SERVER_ERROR.message
                error_code = ErrorCode.SERVER_ERROR.code
            summary = AuditSummary.model_validate(job.summary or {})
            summary.error_code = error_code
            job.summary = summary.to_json_dict()
            if isinstance(exc, AuditHarnessError) and exc.context:
                context_path = self.settings.workspace_root / audit_id / "error_context.json"
                context_path.parent.mkdir(parents=True, exist_ok=True)
                await asyncio.to_thread(
                    context_path.write_text,
                    json.dumps(
                        {
                            "error_type": job.error_type,
                            "error_code": error_code,
                            "public_message": job.error_message,
                            "detail": exc.detail,
                            "context": exc.context,
                        },
                        ensure_ascii=False,
                        indent=2,
                        default=str,
                    ),
                    encoding="utf-8",
                )
                await self._create_artifact(job.id, "error_context_json", context_path)
        finally:
            # Always save a terminal report, even for infrastructure errors. This
            # keeps failed audits inspectable through the artifact endpoints.
            job.updated_at = utc_now()
            job.completed_at = utc_now()
            await self.audit_repo.save(job)
            await self._register_runtime_memory_artifacts(job.id)
            await self._finalize_report(job.id)
            logger.info(
                "AUDIT_DONE | audit_id=%s | status=%s | risk=%s",
                job.id,
                job.status,
                job.risk_level,
            )

    async def _execute_step(
        self,
        *,
        job: AuditJob,
        name: str,
        command: str,
        workspace: Path,
    ) -> AuditStep:
        """Run a single deterministic check command inside the sandbox."""
        step = await self._start_step(job, name, command)
        try:
            result = await self.sandbox_runner.run_command(
                audit_id=job.id,
                step_id=step.id,
                workspace_path=workspace,
                command=command,
                image=job.sandbox_image,
                timeout_seconds=self.settings.default_timeout_seconds,
            )
            return await self._finish_step(step, result)
        except Exception as exc:
            await self._fail_step(step, exc)
            if isinstance(exc, SandboxError):
                raise
            return step

    async def _prepare_resume(self, job: AuditJob) -> dict:
        """Normalize an unfinished job before rerunning it.

        Any step left in ``running`` means the previous process died mid-flight.
        We keep that step as ``interrupted`` evidence, then allow the current run
        to continue from the next safe point.
        """
        summary = {
            "enabled": True,
            "resumed": job.status in {"running", "retrying"},
            "interrupted_steps": [],
            "skipped_steps": [],
        }
        if job.status not in {"running", "retrying"}:
            return summary
        for step in job.steps:
            if step.status != "running":
                continue
            step.status = "interrupted"
            step.exit_code = 1
            step.stderr_tail = truncate_tail(
                "step was interrupted by service restart or task resume",
                self.settings.max_log_chars,
            )
            step.completed_at = utc_now()
            await self.step_repo.save(step)
            summary["interrupted_steps"].append(
                {
                    "step_id": step.id,
                    "name": step.name,
                    "reason": "step was running when audit resumed",
                }
            )
        return summary

    @staticmethod
    def _passed_step(job: AuditJob, name: str, command: str) -> AuditStep | None:
        """Return the latest matching passed step so resume can skip it."""
        candidates = [
            step
            for step in job.steps
            if step.name == name
            and step.command == command
            and step.status == "passed"
        ]
        if not candidates:
            return None
        return sorted(
            candidates,
            key=lambda step: step.completed_at or step.started_at or job.created_at,
        )[-1]

    async def _start_step(
        self,
        job: AuditJob,
        name: str,
        command: str,
    ) -> AuditStep:
        """Persist the start of a sandbox step before invoking Docker."""
        step = AuditStep(
            id=generate_id(),
            audit_id=job.id,
            name=name,
            command=command,
            image=job.sandbox_image,
            status="running",
            findings=[],
            metadata_json={},
            started_at=utc_now(),
        )
        return await self.step_repo.create(step)

    async def _finish_step(self, step: AuditStep, result: dict) -> AuditStep:
        """Persist sandbox output, parsed findings, and the final step status."""
        stdout = result.get("stdout", "")
        stderr = result.get("stderr", "")
        step.container_id = result.get("container_id")
        step.exit_code = result.get("exit_code")
        step.duration_ms = result.get("duration_ms")
        step.stdout_tail = truncate_tail(stdout, self.settings.max_log_chars)
        step.stderr_tail = truncate_tail(stderr, self.settings.max_log_chars)
        step.findings = self.evidence_parser.parse(step.name, stdout, stderr)
        step.status = (
            "timeout"
            if result.get("timed_out")
            else ("passed" if step.exit_code == 0 else "failed")
        )
        step.completed_at = utc_now()
        return await self.step_repo.save(step)

    async def _fail_step(self, step: AuditStep, exc: Exception) -> AuditStep:
        """Convert an execution exception into a failed audit step."""
        step.status = "failed"
        step.exit_code = 1
        step.stderr_tail = truncate_tail(str(exc), self.settings.max_log_chars)
        step.findings = [
            {
                "severity": "high",
                "type": self._error_type(exc),
                "file": None,
                "line": None,
                "message": str(exc),
            }
        ]
        step.completed_at = utc_now()
        return await self.step_repo.save(step)

    async def _write_input_artifacts(
        self,
        job: AuditJob,
        *,
        request: AuditCreateRequest,
        workspace: Path,
        audit_input: Path,
        project_memory: ProjectMemory,
    ) -> None:
        """Write human/debug inputs that make the audit replayable."""
        job_root = workspace.parent
        document_path = job_root / "api_document.md"
        await asyncio.to_thread(
            document_path.write_text,
            request.api_document_markdown,
            encoding="utf-8",
        )
        await self._create_artifact(
            job.id,
            "api_document_markdown",
            document_path,
        )
        patch_path = job_root / "input.patch"
        if request.patch and patch_path.exists():
            await self._create_artifact(job.id, "input_patch", patch_path)
        if request.llm_policy == "mock":
            # Mock rules are copied into audit-input so the sandbox can redirect
            # project LLM calls without requiring code changes from the project.
            mock_config_path = audit_input / "llm_mock_rules.json"
            mock_config = {
                "version": 1,
                "profile": request.llm_mock_profile,
                "rules": [
                    rule.model_dump(mode="json")
                    for rule in self._merge_mock_rules(
                        project_memory.mock_rules,
                        request.llm_mock_rules,
                    )
                ],
                "fallback": (
                    request.llm_mock_fallback.model_dump(mode="json")
                    if request.llm_mock_fallback
                    else None
                ),
            }
            await asyncio.to_thread(
                mock_config_path.write_text,
                json.dumps(mock_config, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            await self._create_artifact(
                job.id,
                "llm_mock_rules_json",
                mock_config_path,
                metadata={
                    "profile": request.llm_mock_profile,
                    "rule_count": len(request.llm_mock_rules),
                },
            )
        memory_snapshot = {
            "loaded_files": project_memory.loaded_files,
            "known_issue_count": project_memory.known_issue_count,
            "warnings": project_memory.warnings,
            "project_markdown": project_memory.project_markdown,
            "known_issues_markdown": project_memory.known_issues_markdown,
        }
        memory_path = audit_input / "project_memory.json"
        await asyncio.to_thread(
            memory_path.write_text,
            json.dumps(memory_snapshot, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        await self._create_artifact(
            job.id,
            "project_memory_json",
            memory_path,
        )

    async def _write_request_artifact(
        self,
        audit_id: str,
        request: AuditCreateRequest,
    ) -> AuditArtifact:
        """Persist the original API request so background jobs can be recovered."""
        job_root = self.settings.workspace_root / audit_id
        job_root.mkdir(parents=True, exist_ok=True)
        path = job_root / "audit_request.json"
        await asyncio.to_thread(
            path.write_text,
            json.dumps(
                request.model_dump(mode="json", exclude_unset=True),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return await self._create_artifact(audit_id, "audit_request_json", path)

    async def _create_artifact(
        self,
        audit_id: str,
        artifact_type: str,
        path: Path,
        metadata: dict | None = None,
    ) -> AuditArtifact:
        """Register a generated file as an externally queryable audit artifact."""
        return await self.artifact_repo.create(
            AuditArtifact(
                id=generate_id(),
                audit_id=audit_id,
                artifact_type=artifact_type,
                path=str(path),
                metadata_json=metadata or {},
                created_at=utc_now(),
            )
        )

    async def _finalize_report(self, audit_id: str) -> None:
        """Build final JSON and Markdown reports and register them as artifacts."""
        job = await self.get(audit_id)
        report_path = self.settings.workspace_root / audit_id / "report.json"
        markdown_path = self.settings.workspace_root / audit_id / "report.md"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        await self._create_artifact(job.id, "report_json", report_path)
        await self._create_artifact(job.id, "report_markdown", markdown_path)
        job = await self.get(audit_id)
        report = self.report_service.build(job)
        self.report_service.write(report, report_path)
        self.report_service.write_markdown(report, markdown_path)

    async def _register_runtime_memory_artifacts(self, audit_id: str) -> None:
        """Attach runtime traces that may have been produced by sandbox helpers."""
        job_root = self.settings.workspace_root / audit_id
        candidates = {
            "execution_events_jsonl": job_root / "execution-events.jsonl",
            "llm_call_traces_jsonl": (
                job_root / "workspace" / ".audit-llm-call-traces.jsonl"
            ),
        }
        job = await self.get(audit_id)
        existing = {artifact.artifact_type for artifact in job.artifacts}
        for artifact_type, path in candidates.items():
            if path.exists() and artifact_type not in existing:
                await self._create_artifact(audit_id, artifact_type, path)

    @staticmethod
    def _read_acceptance_result(path: Path) -> dict:
        """Read acceptance result JSON defensively; missing files mean no results."""
        if (not path.exists() or not path.is_file() or path.is_symlink()
                or not path.resolve().is_relative_to(path.parent.resolve())
                or path.stat().st_size > 2 * 1024 * 1024):
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    @staticmethod
    def _error_type(exc: Exception) -> str:
        """Map exceptions to stable internal error type strings."""
        if isinstance(exc, ApiDocumentError):
            return "api_document_error"
        if isinstance(exc, InvalidInputError):
            return "invalid_input"
        if isinstance(exc, AuditHarnessError):
            return exc.__class__.__name__.removesuffix("Error").lower()
        return exc.__class__.__name__.lower()

    @staticmethod
    def _merge_mock_rules(project_rules: list, request_rules: list) -> list:
        """Let request-provided mock rules override project-memory defaults."""
        merged = {rule.id: rule for rule in project_rules}
        merged.update({rule.id: rule for rule in request_rules})
        return list(merged.values())
