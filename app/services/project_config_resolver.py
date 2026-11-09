import json
import logging
from pathlib import Path
from urllib.parse import urlsplit
from pydantic import ValidationError

from app.core.exceptions import InvalidInputError
from app.schemas.audit import AuditCreateRequest, ProjectConfigInput


class ProjectConfigResolver:
    def apply(self, request: AuditCreateRequest, *, repo_path: Path | None = None) -> None:
        project_config = self._load(request, repo_path=repo_path)
        if project_config is None:
            return

        field_set = request.model_fields_set
        if "runtime" not in field_set and project_config.runtime:
            request.runtime = project_config.runtime
        if "dependency" not in field_set and project_config.dependency:
            request.dependency = project_config.dependency
        if "env_files" not in field_set and project_config.env_files:
            request.env_files = project_config.env_files
        if "llm_adapters" not in field_set and project_config.llm_adapters:
            request.llm_adapters = project_config.llm_adapters
        if "services" not in field_set and project_config.services:
            request.services = project_config.services
        if "setup_commands" not in field_set and project_config.setup_commands:
            request.setup_commands = project_config.setup_commands
        if "startup_command" not in field_set and project_config.startup:
            request.startup_command = project_config.startup.command
        if "service_port" not in field_set and project_config.startup:
            request.service_port = project_config.startup.port
        if "checks" not in field_set and project_config.checks:
            request.checks = project_config.checks

    def _load(self, request: AuditCreateRequest, *, repo_path: Path | None = None) -> ProjectConfigInput | None:
        if repo_path is None and urlsplit(request.repo).scheme == "https":
            # Remote paths are resolved only after safe cloning into a task workspace.
            return None
        repo_path = (repo_path or Path(request.repo)).expanduser().resolve()
        config_path = (repo_path / request.project_config).resolve()
        explicit = "project_config" in request.model_fields_set
        try:
            config_path.relative_to(repo_path)
        except ValueError as exc:
            raise InvalidInputError("project_config must be inside repo") from exc
        if not config_path.exists():
            if explicit:
                raise InvalidInputError(
                    f"project_config not found: {request.project_config}"
                )
            self._append_missing_config_warning(request)
            return None
        if not config_path.is_file() or config_path.stat().st_size > 200_000:
            raise InvalidInputError("project_config must be a JSON file of at most 200000 bytes")
        try:
            payload = json.loads(config_path.read_text(encoding="utf-8"))
            return ProjectConfigInput.model_validate(payload)
        except (json.JSONDecodeError, ValidationError) as exc:
            raise InvalidInputError(
                f"project_config is not valid JSON: {request.project_config}"
            ) from exc

    @staticmethod
    def _append_missing_config_warning(request: AuditCreateRequest) -> None:
        warning = (
            f"default project_config not found: {request.project_config}; "
            "falling back to request fields and auto detection"
        )
        if warning not in request.project_config_warnings:
            request.project_config_warnings.append(warning)
        logging.getLogger("audit_harness.audit").warning(
            "PROJECT_CONFIG_MISSING | repo=%s | project_config=%s "
            "| fallback=auto_detection",
            request.repo,
            request.project_config,
        )
