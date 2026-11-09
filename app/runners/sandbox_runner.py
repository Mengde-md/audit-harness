from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class SandboxRunner(ABC):
    @abstractmethod
    async def run_command(
        self,
        *,
        audit_id: str,
        step_id: str,
        workspace_path: Path,
        command: str,
        image: str,
        timeout_seconds: int,
        audit_input_path: Path | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def run_acceptance(
        self,
        *,
        audit_id: str,
        step_id: str,
        workspace_path: Path,
        audit_input_path: Path,
        startup_command: str,
        service_port: int,
        llm_policy: str,
        env_files: list[str],
        llm_adapters: list[dict[str, Any]],
        services: dict[str, Any] | None,
        setup_commands: list[str],
        image: str,
        timeout_seconds: int,
        startup_timeout_seconds: int,
    ) -> dict[str, Any]:
        raise NotImplementedError
