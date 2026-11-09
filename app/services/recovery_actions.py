from dataclasses import dataclass

from app.core.config import Settings


@dataclass(frozen=True)
class RecoveryAction:
    """Allowlisted recovery action and its deterministic safety metadata."""

    name: str
    startup_timeout_extra_seconds: int = 0
    idempotent: bool = True
    low_risk: bool = True

    def startup_timeout_seconds(self, settings: Settings) -> int:
        return (
            settings.service_startup_timeout_seconds
            + self.startup_timeout_extra_seconds
        )


class RecoveryActionRegistry:
    """Registry is the hard allowlist for executable recovery actions."""

    def __init__(self):
        self._actions = {
            "rerun_acceptance_after_service_ready_wait": RecoveryAction(
                name="rerun_acceptance_after_service_ready_wait",
                startup_timeout_extra_seconds=15,
            ),
            "rerun_acceptance_with_same_inputs": RecoveryAction(
                name="rerun_acceptance_with_same_inputs",
                startup_timeout_extra_seconds=15,
            ),
            "rerun_acceptance_with_fresh_containers": RecoveryAction(
                name="rerun_acceptance_with_fresh_containers",
            ),
            "rerun_acceptance_once": RecoveryAction(
                name="rerun_acceptance_once",
            ),
            "none": RecoveryAction(name="none"),
        }

    def get(self, name: str) -> RecoveryAction:
        return self._actions.get(name, self._actions["none"])

    def is_safe(self, name: str) -> bool:
        """Return whether an action may consume automated retry budget."""
        action = self._actions.get(name)
        return bool(
            action
            and action.name != "none"
            and action.idempotent
            and action.low_risk
        )


class RecoveryPolicy:
    """Deterministically grant retry budget; prompt text has no authority."""

    def __init__(self, settings: Settings, registry: RecoveryActionRegistry):
        self.settings = settings
        self.registry = registry

    def max_attempts(self, candidates: list[dict]) -> int:
        """Grant at most one non-stacking extra attempt for safe HIGH memory."""
        base = max(0, self.settings.recovery_base_retry_count)
        has_safe_high = any(
            candidate.get("trust_level") == "HIGH"
            and self.registry.is_safe(str(candidate.get("action", "")))
            for candidate in candidates
        )
        extra = (
            self.settings.recovery_high_extra_retry_count if has_safe_high else 0
        )
        return min(base + extra, self.settings.recovery_hard_max_attempts)

    def executable_actions(self, candidates: list[dict]) -> list[RecoveryAction]:
        """Filter unknown, unsafe, and duplicate actions while preserving order."""
        actions: list[RecoveryAction] = []
        seen: set[str] = set()
        for candidate in candidates:
            name = str(candidate.get("action", ""))
            if name in seen or not self.registry.is_safe(name):
                continue
            seen.add(name)
            actions.append(self.registry.get(name))
        return actions
