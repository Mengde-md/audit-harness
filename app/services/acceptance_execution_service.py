"""Acceptance execution and self-healing retry loop."""

from pathlib import Path

from app.core.config import Settings
from app.core.exceptions import SandboxError
from app.memory.recovery_memory import RecoveryAdvisor
from app.memory.task_memory import TaskMemoryWriter
from app.models.audit_job import AuditJob
from app.models.audit_step import AuditStep
from app.parsers.evidence_parser import EvidenceParser
from app.repositories.step_repository import StepRepository
from app.runners.sandbox_runner import SandboxRunner
from app.schemas.audit import AuditCreateRequest
from app.services.recovery_actions import RecoveryActionRegistry, RecoveryPolicy
from app.utils.id_generator import generate_id
from app.utils.process import truncate_tail
from app.utils.time import utc_now


class AcceptanceExecutionService:
    """Run acceptance and a bounded, memory-informed recovery loop."""

    def __init__(
        self,
        *,
        settings: Settings,
        step_repo: StepRepository,
        sandbox_runner: SandboxRunner,
        evidence_parser: EvidenceParser,
        recovery_advisor: RecoveryAdvisor,
    ):
        self.settings = settings
        self.step_repo = step_repo
        self.sandbox_runner = sandbox_runner
        self.evidence_parser = evidence_parser
        self.recovery_advisor = recovery_advisor
        self.recovery_actions = RecoveryActionRegistry()
        self.recovery_policy = RecoveryPolicy(settings, self.recovery_actions)

    async def execute(
        self,
        *,
        job: AuditJob,
        request: AuditCreateRequest,
        workspace: Path,
        audit_input: Path,
        task_memory: TaskMemoryWriter,
    ) -> tuple[AuditStep, dict]:
        """Run acceptance, classify failures, and retry when policy allows it."""
        llm_adapters = [
            adapter.model_dump(mode="json")
            for adapter in request.llm_adapters
        ]
        services = request.services.model_dump(mode="json")
        recovery_summary = {
            "enabled": True,
            "attempts": [],
            "decision": None,
        }
        existing_acceptance = self._latest_passed_acceptance(job)
        if existing_acceptance is not None:
            # Resume path: if a previous acceptance step already passed, the
            # current run should not restart the audited service just to confirm it.
            recovery_summary["decision"] = {
                "retry": False,
                "reason": "previous acceptance step already passed",
                "resumed": True,
            }
            task_memory.append(
                "acceptance_step_skipped",
                step_id=existing_acceptance.id,
                reason="previous acceptance step already passed",
            )
            return existing_acceptance, recovery_summary
        acceptance = await self._execute_step(
            job=job,
            workspace=workspace,
            audit_input=audit_input,
            llm_policy=request.llm_policy,
            env_files=request.env_files,
            llm_adapters=llm_adapters,
            services=services,
            setup_commands=request.setup_commands,
        )
        if acceptance.exit_code == 0:
            # Fast path: no recovery memory is written for a clean first pass.
            recovery_summary["decision"] = {
                "retry": False,
                "reason": "initial acceptance passed",
            }
            return acceptance, recovery_summary

        classification = self.recovery_advisor.classify_acceptance_failure(
            {
                "stdout": acceptance.stdout_tail,
                "stderr": acceptance.stderr_tail,
                "timed_out": acceptance.status == "timeout",
            }
        )
        # The signature deliberately includes environment fingerprints and a
        # concrete error pattern so historical retries do not over-generalize.
        signature = self.recovery_advisor.build_signature(
            stage="api_acceptance",
            category=classification.category,
            error_pattern=classification.error_pattern,
            profile=request.profile,
            services=services,
            setup_commands=request.setup_commands,
            startup_command=job.startup_command,
        )
        recommendation = await self.recovery_advisor.recommend(
            repo=request.repo,
            signature=signature,
        )
        recovery_summary["decision"] = {
            "category": classification.category,
            "error_pattern": classification.error_pattern,
            "signature": signature.to_dict(),
            **recommendation,
        }
        task_memory.append(
            "recovery_decision",
            category=classification.category,
            error_pattern=classification.error_pattern,
            signature=signature.key,
            match_key=signature.match_key,
            retry=recommendation["retry"],
            action=recommendation["action"],
            reason=recommendation["reason"],
            memory_source=recommendation["memory_source"],
            guidance=recommendation["guidance"],
        )
        if not recommendation["retry"]:
            return acceptance, recovery_summary

        candidates = recommendation["actions"]
        budget = self.recovery_policy.max_attempts(candidates)
        actions = self.recovery_policy.executable_actions(candidates)[:budget]
        recovery_summary["decision"]["retry_budget"] = budget
        recovery_summary["decision"]["candidate_actions"] = [
            item["action"] for item in candidates
        ]
        recovery_summary["decision"]["executable_actions"] = [
            item.name for item in actions
        ]
        current = acceptance
        for attempt_number, action in enumerate(actions, start=1):
            # Each round executes one allowlisted action and re-validates. A
            # successful round ends the loop; failed actions are task facts only.
            current = await self._execute_step(
                job=job,
                name=f"api_acceptance_retry_{attempt_number}",
                workspace=workspace,
                audit_input=audit_input,
                llm_policy=request.llm_policy,
                env_files=request.env_files,
                llm_adapters=llm_adapters,
                services=services,
                setup_commands=request.setup_commands,
                startup_timeout_seconds=action.startup_timeout_seconds(self.settings),
            )
            outcome = "passed" if current.exit_code == 0 else "failed"
            attempt = {
                "stage": "api_acceptance",
                "category": classification.category,
                "error_pattern": classification.error_pattern,
                "action": action.name,
                "attempt": attempt_number,
                "result": outcome,
                "step_id": current.id,
                "duration_ms": current.duration_ms,
            }
            recovery_summary["attempts"].append(attempt)
            task_memory.append("recovery_attempt_completed", **attempt)
            if outcome == "passed":
                await self.recovery_advisor.store.record_success(
                    repo=request.repo,
                    signature=signature,
                    action=action.name,
                    audit_id=job.id,
                    duration_ms=current.duration_ms,
                    runtime=request.runtime.language,
                    framework=None,
                    services=self._enabled_services(services),
                )
                break
        return current, recovery_summary

    @staticmethod
    def _enabled_services(services: dict) -> list[str]:
        """Return stable service names without leaking project configuration."""
        return sorted(
            name
            for name, config in services.items()
            if isinstance(config, dict) and config.get("enabled") is True
        )

    @staticmethod
    def _latest_passed_acceptance(job: AuditJob) -> AuditStep | None:
        """Return a previous passed acceptance step for resumable execution."""
        candidates = [
            step
            for step in job.steps
            if step.name.startswith("api_acceptance") and step.status == "passed"
        ]
        if not candidates:
            return None
        return sorted(
            candidates,
            key=lambda step: step.completed_at or step.started_at or job.created_at,
        )[-1]

    async def _execute_step(
        self,
        *,
        job: AuditJob,
        workspace: Path,
        audit_input: Path,
        llm_policy: str,
        env_files: list[str],
        llm_adapters: list[dict],
        services: dict,
        setup_commands: list[str],
        name: str = "api_acceptance",
        startup_timeout_seconds: int | None = None,
    ) -> AuditStep:
        """Invoke the sandbox acceptance runner and persist the resulting step."""
        step = await self._start_step(job, name)
        try:
            result = await self.sandbox_runner.run_acceptance(
                audit_id=job.id,
                step_id=step.id,
                workspace_path=workspace,
                audit_input_path=audit_input,
                startup_command=job.startup_command,
                service_port=job.service_port,
                llm_policy=llm_policy,
                env_files=env_files,
                llm_adapters=llm_adapters,
                services=services,
                setup_commands=setup_commands,
                image=job.sandbox_image,
                timeout_seconds=self.settings.default_timeout_seconds,
                startup_timeout_seconds=(
                    startup_timeout_seconds
                    if startup_timeout_seconds is not None
                    else self.settings.service_startup_timeout_seconds
                ),
            )
            return await self._finish_step(step, result)
        except Exception as exc:
            await self._fail_step(step, exc)
            if isinstance(exc, SandboxError):
                raise
            return step

    async def _start_step(self, job: AuditJob, name: str) -> AuditStep:
        """Create a running acceptance step before the sandbox call starts."""
        step = AuditStep(
            id=generate_id(),
            audit_id=job.id,
            name=name,
            command=job.startup_command,
            image=job.sandbox_image,
            status="running",
            findings=[],
            metadata_json={},
            started_at=utc_now(),
        )
        return await self.step_repo.create(step)

    async def _finish_step(self, step: AuditStep, result: dict) -> AuditStep:
        """Persist acceptance stdout/stderr tails, findings, and status."""
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
        """Record unexpected acceptance infrastructure errors as findings."""
        step.status = "failed"
        step.exit_code = 1
        step.stderr_tail = truncate_tail(str(exc), self.settings.max_log_chars)
        step.findings = [
            {
                "severity": "high",
                "type": exc.__class__.__name__.lower(),
                "file": None,
                "line": None,
                "message": str(exc),
            }
        ]
        step.completed_at = utc_now()
        return await self.step_repo.save(step)
