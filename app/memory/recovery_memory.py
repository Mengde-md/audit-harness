"""Structured recovery memory for the self-healing audit loop.

Project memory uses exact signatures. Cross-repository experience uses a
coarser match key and contains only aggregated successful actions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.memory.constants import TRUST_RANK, MemoryScope, MemorySource, TrustLevel
from app.models.cross_recovery_candidate import CrossRecoveryCandidate
from app.models.cross_recovery_experience import CrossRecoveryExperience
from app.models.project_recovery_memory import ProjectRecoveryMemory
from app.utils.id_generator import generate_id
from app.utils.process import sha256_text
from app.utils.time import utc_now

RECOVERABLE_CATEGORIES = {
    "DB_READY_ERROR",
    "SERVICE_READY_ERROR",
    "TEMP_ENV_ERROR",
    "API_TIMEOUT_ERROR",
}
RECOVERY_EXPERIENCE_KIND = "recovery_prior"


@dataclass(frozen=True)
class FailureClassification:
    """Normalized failure category plus a precise reusable pattern."""

    category: str
    error_pattern: str


@dataclass(frozen=True)
class RecoverySignature:
    """Project-sensitive signature plus a cross-project pattern key."""

    stage: str
    category: str
    error_pattern: str
    profile: str
    service_fingerprint: str
    setup_fingerprint: str
    startup_fingerprint: str

    @property
    def key(self) -> str:
        """Hash all fields that materially change project recovery behavior."""
        return sha256_text(
            json.dumps(self._key_payload(), ensure_ascii=False, sort_keys=True)
        ).removeprefix("sha256:")

    @property
    def match_key(self) -> str:
        """Build the coarse key used only for cross-repository aggregation."""
        return ":".join(
            (
                RECOVERY_EXPERIENCE_KIND,
                self.stage,
                self.category,
                self.error_pattern,
            )
        )

    def to_dict(self) -> dict[str, str]:
        """Expose signature details for reports and persisted evidence."""
        return {
            "key": self.key,
            "match_key": self.match_key,
            **self._key_payload(),
        }

    def _key_payload(self) -> dict[str, str]:
        return {
            "stage": self.stage,
            "category": self.category,
            "error_pattern": self.error_pattern,
            "profile": self.profile,
            "service_fingerprint": self.service_fingerprint,
            "setup_fingerprint": self.setup_fingerprint,
            "startup_fingerprint": self.startup_fingerprint,
        }


class RecoveryMemoryStore:
    """Persist project recovery memory and promoted cross-project experience."""

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
    ) -> None:
        self.session = session
        self.settings = settings
        self.legacy_root = settings.recovery_memory_root

    async def list_project_actions(
        self,
        *,
        repo: str,
        signature: RecoverySignature,
    ) -> list[dict[str, Any]]:
        """Return exact-signature actions for one repository."""
        await self._migrate_legacy_project(repo)
        result = await self.session.execute(
            select(ProjectRecoveryMemory).where(
                ProjectRecoveryMemory.repo_hash == self.repo_hash(repo),
                ProjectRecoveryMemory.signature_key == signature.key,
            )
        )
        return [self._project_record(item) for item in result.scalars().all()]

    async def list_cross_actions(
        self,
        *,
        signature: RecoverySignature,
    ) -> list[dict[str, Any]]:
        """Return runtime-eligible shared experience for a coarse match key."""
        result = await self.session.execute(
            select(CrossRecoveryExperience).where(
                CrossRecoveryExperience.match_key == signature.match_key,
                CrossRecoveryExperience.enabled.is_(True),
                CrossRecoveryExperience.repo_count
                >= self.settings.cross_recovery_middle_repo_count,
                CrossRecoveryExperience.success_count
                >= self.settings.cross_recovery_middle_success_count,
            )
        )
        return [self._cross_record(item) for item in result.scalars().all()]

    async def record_success(
        self,
        *,
        repo: str,
        signature: RecoverySignature,
        action: str,
        audit_id: str,
        duration_ms: int | None,
        runtime: str | None,
        framework: str | None,
        services: list[str],
    ) -> dict[str, Any]:
        """Atomically update project memory, candidate stats, and experience."""
        now = utc_now()
        repo_hash = self.repo_hash(repo)
        project = await self._get_project_record(repo_hash, signature.key, action)
        if project is None:
            project = ProjectRecoveryMemory(
                memory_id=generate_id(),
                scope=MemoryScope.SINGLE_REPO.value,
                source=MemorySource.SYSTEM.value,
                trust_level=TrustLevel.MIDDLE.value,
                repo_hash=repo_hash,
                signature_key=signature.key,
                match_key=signature.match_key,
                stage=signature.stage,
                category=signature.category,
                pattern_key=signature.error_pattern,
                profile=signature.profile,
                runtime=runtime,
                framework=framework,
                services_json=services,
                action=action,
                success_count=0,
                avg_duration_ms=None,
                signature_json=signature.to_dict(),
                content_json={},
                created_at=now,
                updated_at=now,
            )
            self.session.add(project)

        previous_count = project.success_count
        project.success_count += 1
        project.trust_level = self._project_trust(project.success_count).value
        project.updated_at = now
        project.content_json = {
            "summary": (
                f"{signature.error_pattern} recovered with {action} "
                f"{project.success_count} time(s) in this repository."
            ),
            "last_audit_id": audit_id,
        }
        if duration_ms is not None:
            project.avg_duration_ms = self._next_average(
                project.avg_duration_ms,
                previous_count,
                duration_ms,
            )

        candidate = await self._increment_candidate(
            repo_hash=repo_hash,
            signature=signature,
            action=action,
            increment=1,
            now=now,
        )
        await self._promote_candidate(candidate, now)
        await self.session.commit()
        await self.session.refresh(project)
        return self._project_record(project)

    async def _get_project_record(
        self,
        repo_hash: str,
        signature_key: str,
        action: str,
    ) -> ProjectRecoveryMemory | None:
        result = await self.session.execute(
            select(ProjectRecoveryMemory).where(
                ProjectRecoveryMemory.repo_hash == repo_hash,
                ProjectRecoveryMemory.signature_key == signature_key,
                ProjectRecoveryMemory.action == action,
            )
        )
        return result.scalar_one_or_none()

    async def _increment_candidate(
        self,
        *,
        repo_hash: str,
        signature: RecoverySignature,
        action: str,
        increment: int,
        now,
    ) -> CrossRecoveryCandidate:
        result = await self.session.execute(
            select(CrossRecoveryCandidate).where(
                CrossRecoveryCandidate.match_key == signature.match_key,
                CrossRecoveryCandidate.action == action,
            )
        )
        candidate = result.scalar_one_or_none()
        if candidate is None:
            candidate = CrossRecoveryCandidate(
                match_key=signature.match_key,
                action=action,
                kind=RECOVERY_EXPERIENCE_KIND,
                stage=signature.stage,
                category=signature.category,
                pattern_key=signature.error_pattern,
                repo_count=1,
                success_count=increment,
                repo_hashes=[repo_hash],
                first_seen_at=now,
                last_seen_at=now,
            )
            self.session.add(candidate)
            return candidate

        repo_hashes = list(candidate.repo_hashes or [])
        if repo_hash not in repo_hashes:
            repo_hashes.append(repo_hash)
            candidate.repo_hashes = repo_hashes
            candidate.repo_count = len(repo_hashes)
        candidate.success_count += increment
        candidate.last_seen_at = now
        return candidate

    async def _promote_candidate(
        self,
        candidate: CrossRecoveryCandidate,
        now,
    ) -> None:
        if (
            candidate.repo_count < self.settings.cross_recovery_deposit_repo_count
            or candidate.success_count
            < self.settings.cross_recovery_deposit_success_count
        ):
            return
        result = await self.session.execute(
            select(CrossRecoveryExperience).where(
                CrossRecoveryExperience.match_key == candidate.match_key,
                CrossRecoveryExperience.action == candidate.action,
            )
        )
        experience = result.scalar_one_or_none()
        trust = self._cross_trust(candidate.repo_count, candidate.success_count)
        content = {
            "summary": (
                f"{candidate.action} recovered {candidate.pattern_key} "
                f"{candidate.success_count} time(s) across "
                f"{candidate.repo_count} repositories."
            )
        }
        if experience is None:
            experience = CrossRecoveryExperience(
                memory_id=generate_id(),
                scope=MemoryScope.CROSS_REPO.value,
                source=MemorySource.SYSTEM.value,
                trust_level=trust.value,
                match_key=candidate.match_key,
                action=candidate.action,
                kind=candidate.kind,
                stage=candidate.stage,
                category=candidate.category,
                pattern_key=candidate.pattern_key,
                repo_count=candidate.repo_count,
                repo_hashes=list(candidate.repo_hashes or []),
                success_count=candidate.success_count,
                content_json=content,
                enabled=True,
                created_at=now,
                updated_at=now,
            )
            self.session.add(experience)
            return
        experience.trust_level = trust.value
        experience.repo_count = candidate.repo_count
        experience.repo_hashes = list(candidate.repo_hashes or [])
        experience.success_count = candidate.success_count
        experience.content_json = content
        experience.updated_at = now

    async def _migrate_legacy_project(self, repo: str) -> None:
        """Import one old project JSON once, retaining it as a backup."""
        path = self.legacy_root / f"{self.repo_hash(repo)}.json"
        migrated_path = path.with_suffix(".json.migrated")
        if not path.exists() or migrated_path.exists():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return

        now = utc_now()
        for legacy in (payload.get("signatures") or {}).values():
            signature_data = legacy.get("signature") or {}
            signature = self._signature_from_legacy(signature_data)
            if signature is None:
                continue
            for action, action_data in (legacy.get("actions") or {}).items():
                success_count = int(action_data.get("success_count", 0) or 0)
                if success_count <= 0:
                    continue
                existing = await self._get_project_record(
                    self.repo_hash(repo), signature.key, action
                )
                if existing is not None:
                    continue
                project = ProjectRecoveryMemory(
                    memory_id=generate_id(),
                    scope=MemoryScope.SINGLE_REPO.value,
                    source=MemorySource.SYSTEM.value,
                    trust_level=self._project_trust(success_count).value,
                    repo_hash=self.repo_hash(repo),
                    signature_key=signature.key,
                    match_key=signature.match_key,
                    stage=signature.stage,
                    category=signature.category,
                    pattern_key=signature.error_pattern,
                    profile=signature.profile,
                    runtime=None,
                    framework=None,
                    services_json=[],
                    action=action,
                    success_count=success_count,
                    avg_duration_ms=action_data.get("avg_duration_ms"),
                    signature_json=signature.to_dict(),
                    content_json={
                        "summary": "Imported from legacy recovery memory.",
                        "last_audit_id": action_data.get("last_audit_id"),
                    },
                    created_at=now,
                    updated_at=now,
                )
                self.session.add(project)
                candidate = await self._increment_candidate(
                    repo_hash=self.repo_hash(repo),
                    signature=signature,
                    action=action,
                    increment=success_count,
                    now=now,
                )
                await self._promote_candidate(candidate, now)
        await self.session.commit()
        try:
            path.replace(migrated_path)
        except OSError:
            # Imported rows remain authoritative even if backup renaming fails.
            pass

    @staticmethod
    def _signature_from_legacy(data: dict[str, Any]) -> RecoverySignature | None:
        required = (
            "stage",
            "category",
            "error_pattern",
            "profile",
            "service_fingerprint",
            "setup_fingerprint",
            "startup_fingerprint",
        )
        if not all(isinstance(data.get(field), str) for field in required):
            return None
        return RecoverySignature(**{field: data[field] for field in required})

    def _project_trust(self, success_count: int) -> TrustLevel:
        if success_count >= self.settings.project_recovery_high_success_count:
            return TrustLevel.HIGH
        return TrustLevel.MIDDLE

    def _cross_trust(self, repo_count: int, success_count: int) -> TrustLevel:
        if (
            repo_count >= self.settings.cross_recovery_high_repo_count
            and success_count >= self.settings.cross_recovery_high_success_count
        ):
            return TrustLevel.HIGH
        return TrustLevel.MIDDLE

    @staticmethod
    def _next_average(previous: int | None, previous_count: int, value: int) -> int:
        if previous is None or previous_count <= 0:
            return value
        return round(((previous * previous_count) + value) / (previous_count + 1))

    @staticmethod
    def repo_hash(repo: str) -> str:
        """Hash an absolute repository path without the sha256 prefix."""
        return sha256_text(str(Path(repo).expanduser().resolve())).removeprefix(
            "sha256:"
        )

    @staticmethod
    def _project_record(item: ProjectRecoveryMemory) -> dict[str, Any]:
        return {
            "memory_id": item.memory_id,
            "scope": item.scope,
            "source": item.source,
            "trust_level": item.trust_level,
            "action": item.action,
            "success_count": item.success_count,
            "avg_duration_ms": item.avg_duration_ms,
            "content": item.content_json,
            "memory_source": "project_recovery",
        }

    @staticmethod
    def _cross_record(item: CrossRecoveryExperience) -> dict[str, Any]:
        return {
            "memory_id": item.memory_id,
            "scope": item.scope,
            "source": item.source,
            "trust_level": item.trust_level,
            "action": item.action,
            "repo_count": item.repo_count,
            "success_count": item.success_count,
            "content": item.content_json,
            "memory_source": "cross_recovery",
        }


class RecoveryAdvisor:
    """Classify failures and rank project, shared, and default actions."""

    def __init__(self, store: RecoveryMemoryStore):
        self.store = store

    def build_signature(
        self,
        *,
        stage: str,
        category: str,
        error_pattern: str,
        profile: str,
        services: dict[str, Any],
        setup_commands: list[str],
        startup_command: str,
    ) -> RecoverySignature:
        """Build the project/environment-sensitive failure signature."""
        return RecoverySignature(
            stage=stage,
            category=category,
            error_pattern=error_pattern,
            profile=profile,
            service_fingerprint=self._fingerprint(services),
            setup_fingerprint=self._fingerprint(setup_commands),
            startup_fingerprint=sha256_text(startup_command).removeprefix("sha256:"),
        )

    async def recommend(
        self,
        *,
        repo: str,
        signature: RecoverySignature,
    ) -> dict[str, Any]:
        """Return ordered actions without granting execution permission."""
        if signature.category not in RECOVERABLE_CATEGORIES:
            return {
                "retry": False,
                "action": "none",
                "actions": [],
                "reason": f"{signature.category} is not recoverable by policy",
                "memory_source": "none",
                "guidance": [],
            }

        actions = await self.store.list_project_actions(repo=repo, signature=signature)
        memory_source = "project_recovery"
        if not actions:
            actions = await self.store.list_cross_actions(signature=signature)
            memory_source = "cross_recovery" if actions else "default"

        default_action = self.default_action(signature.category)
        if default_action != "none" and all(
            item["action"] != default_action for item in actions
        ):
            actions.append(
                {
                    "trust_level": TrustLevel.LOW.value,
                    "action": default_action,
                    "repo_count": 0,
                    "success_count": 0,
                    "memory_source": "default",
                    "content": {},
                }
            )
        actions.sort(key=self._action_sort_key, reverse=True)
        guidance = [
            self._guidance(item)
            for item in actions
            if item.get("trust_level") in {
                TrustLevel.MIDDLE.value,
                TrustLevel.HIGH.value,
            }
        ]
        return {
            "retry": bool(actions),
            "action": actions[0]["action"] if actions else "none",
            "actions": actions,
            "reason": self._reason(memory_source),
            "memory_source": memory_source,
            "guidance": guidance,
        }

    @staticmethod
    def classify_acceptance_failure(result: dict[str, Any]) -> FailureClassification:
        """Convert raw logs into a coarse category and precise pattern."""
        stdout = str(result.get("stdout", ""))
        stderr = str(result.get("stderr", ""))
        combined = f"{stdout}\n{stderr}".lower()
        if (
            "unixhttpconnectionpool" in combined
            or "docker operation failed" in combined
            or "docker.errors" in combined
        ):
            return FailureClassification("TEMP_ENV_ERROR", "docker_api_error")
        if result.get("timed_out"):
            return FailureClassification("API_TIMEOUT_ERROR", "timeout")
        if "modulenotfounderror" in combined or "no module named" in combined:
            return FailureClassification("PROJECT_CODE_ERROR", "python_module_missing")
        if "importerror" in combined:
            return FailureClassification("PROJECT_CODE_ERROR", "python_import_error")
        if "address already in use" in combined:
            return FailureClassification("SERVICE_READY_ERROR", "port_in_use")
        if "password authentication failed" in combined:
            return FailureClassification("DB_READY_ERROR", "db_auth_failed")
        if "does not exist" in combined and "database" in combined:
            return FailureClassification("DB_READY_ERROR", "db_missing")
        if "audit_setup_failed" in combined or "alembic" in combined:
            return FailureClassification("DB_READY_ERROR", "db_setup_failed")
        if "connection refused" in combined:
            return FailureClassification("DB_READY_ERROR", "connection_refused")
        if "database" in combined:
            return FailureClassification("DB_READY_ERROR", "database_error")
        if "docker" in combined or "container" in combined:
            return FailureClassification("TEMP_ENV_ERROR", "container_error")
        if "application startup complete" not in combined:
            return FailureClassification("SERVICE_READY_ERROR", "service_not_ready")
        if "permission denied" in combined:
            return FailureClassification("TEMP_ENV_ERROR", "permission_denied")
        if "timeout" in combined or "timed out" in combined:
            return FailureClassification("API_TIMEOUT_ERROR", "timeout")
        return FailureClassification("PROJECT_CODE_ERROR", "unknown")

    @staticmethod
    def default_action(category: str) -> str:
        return {
            "DB_READY_ERROR": "rerun_acceptance_after_service_ready_wait",
            "SERVICE_READY_ERROR": "rerun_acceptance_with_same_inputs",
            "TEMP_ENV_ERROR": "rerun_acceptance_with_fresh_containers",
            "API_TIMEOUT_ERROR": "rerun_acceptance_once",
        }.get(category, "none")

    @staticmethod
    def _action_sort_key(item: dict[str, Any]) -> tuple[int, int, int]:
        trust = TrustLevel(item.get("trust_level", TrustLevel.LOW.value))
        return (
            TRUST_RANK[trust],
            int(item.get("repo_count", 0) or 0),
            int(item.get("success_count", 0) or 0),
        )

    @staticmethod
    def _guidance(item: dict[str, Any]) -> dict[str, Any]:
        high = item["trust_level"] == TrustLevel.HIGH.value
        return {
            "section": "重点参考" if high else "谨慎参考",
            "memory_source": item["memory_source"],
            "trust_level": item["trust_level"],
            "action": item["action"],
            "message": (
                "This action has strong historical evidence; verify its "
                "preconditions and prioritize it."
                if high
                else "This action has limited historical evidence; verify all "
                "current preconditions before using it."
            ),
        }

    @staticmethod
    def _reason(memory_source: str) -> str:
        return {
            "project_recovery": "exact project recovery memory matched",
            "cross_recovery": "cross-project recovery experience matched",
            "default": "default conservative recovery action",
        }[memory_source]

    @staticmethod
    def _fingerprint(value: Any) -> str:
        return sha256_text(
            json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        ).removeprefix("sha256:")
