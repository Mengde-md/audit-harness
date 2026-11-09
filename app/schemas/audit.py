from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.config import get_settings
from app.schemas.mock_llm import LlmMockResponse, LlmMockRule


class CheckInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    command: str = Field(min_length=1, max_length=500)


class DependencyInput(BaseModel):
    type: Literal["python"] = "python"
    manifest: str = Field(min_length=1, max_length=255)


class RuntimeInput(BaseModel):
    language: Literal["python"] = "python"
    version: Literal["3.10", "3.11", "3.12"] = "3.11"


class EnvFileInput(BaseModel):
    path: str = Field(min_length=1, max_length=255)


class LlmAdapterInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    type: Literal["chat", "embedding"] = "chat"
    protocol: Literal["openai", "anthropic"] = "openai"
    base_url_env: str = Field(min_length=1, max_length=100)
    api_key_env: str = Field(min_length=1, max_length=100)
    model_env: str | None = Field(default=None, max_length=100)


class StartupInput(BaseModel):
    command: str = Field(min_length=1, max_length=500)
    port: int = Field(ge=1, le=65535)


class PostgresServiceInput(BaseModel):
    enabled: bool = False
    variant: Literal["pg16", "pg16-vector"] = "pg16"


class RedisServiceInput(BaseModel):
    enabled: bool = False


class ManagedServicesInput(BaseModel):
    postgres: PostgresServiceInput = Field(default_factory=PostgresServiceInput)
    redis: RedisServiceInput = Field(default_factory=RedisServiceInput)

    @property
    def enabled(self) -> bool:
        return self.postgres.enabled or self.redis.enabled


class ProjectConfigInput(BaseModel):
    version: Literal[1] = 1
    runtime: RuntimeInput | None = None
    dependency: DependencyInput | None = None
    env_files: list[str] = Field(default_factory=list, max_length=20)
    llm_adapters: list[LlmAdapterInput] = Field(default_factory=list, max_length=20)
    services: ManagedServicesInput | None = None
    setup_commands: list[str] = Field(default_factory=list, max_length=10)
    startup: StartupInput | None = None
    checks: list[CheckInput] | None = None


class AuditCreateRequest(BaseModel):
    requester_id: str = Field(min_length=1, max_length=255)
    repo: str = Field(min_length=1)
    audit_mode: Literal["change", "full"]
    llm_policy: Literal["mock", "disabled"]
    llm_mock_profile: Literal["generic-agent"] | None = "generic-agent"
    llm_mock_rules: list[LlmMockRule] = Field(default_factory=list, max_length=100)
    llm_mock_fallback: LlmMockResponse | None = None
    base_commit: str | None = Field(default=None, max_length=255)
    patch: str | None = None
    api_document_markdown: str = Field(min_length=1)
    project_config: str = Field(
        default=".audit-harness/project.json",
        min_length=1,
        max_length=255,
    )
    project_config_warnings: list[str] = Field(default_factory=list, max_length=20)
    runtime: RuntimeInput = Field(default_factory=RuntimeInput)
    dependency: DependencyInput | None = None
    env_files: list[str] = Field(default_factory=list, max_length=20)
    llm_adapters: list[LlmAdapterInput] = Field(default_factory=list, max_length=20)
    startup_command: str | None = Field(default=None, min_length=1, max_length=500)
    service_port: int | None = Field(default=None, ge=1, le=65535)
    profile: Literal["python-basic", "custom"] = "python-basic"
    services: ManagedServicesInput = Field(default_factory=ManagedServicesInput)
    setup_commands: list[str] = Field(default_factory=list, max_length=10)
    checks: list[CheckInput] | None = None

    @field_validator("setup_commands")
    @classmethod
    def validate_setup_commands(cls, value: list[str]) -> list[str]:
        for command in value:
            if not command or len(command) > 500:
                raise ValueError(
                    "each setup command must be between 1 and 500 characters"
                )
        return value

    @field_validator("patch")
    @classmethod
    def validate_patch_size(cls, value: str | None) -> str | None:
        if value and len(value) > get_settings().max_patch_chars:
            raise ValueError("patch exceeds MAX_PATCH_CHARS")
        return value

    @field_validator("api_document_markdown")
    @classmethod
    def validate_document_size(cls, value: str) -> str:
        if len(value) > get_settings().max_api_document_chars:
            raise ValueError("api document exceeds MAX_API_DOCUMENT_CHARS")
        return value

    @model_validator(mode="after")
    def validate_audit_mode_inputs(self) -> "AuditCreateRequest":
        if self.audit_mode == "change":
            if not self.base_commit or not self.patch:
                raise ValueError(
                    "change mode requires both base_commit and patch"
                )
        elif self.base_commit is not None or self.patch is not None:
            raise ValueError(
                "full mode requires base_commit and patch to be omitted"
            )
        rule_ids = [rule.id for rule in self.llm_mock_rules]
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("llm_mock_rules ids must be unique")
        explicit_mock_fields = {
            "llm_mock_profile",
            "llm_mock_rules",
            "llm_mock_fallback",
        } & self.model_fields_set
        if self.llm_policy == "disabled" and explicit_mock_fields:
            raise ValueError(
                "disabled llm_policy requires mock profile, rules, and fallback "
                "to be omitted"
            )
        if (
            self.llm_policy == "mock"
            and self.llm_mock_profile is None
            and self.llm_mock_fallback is None
        ):
            raise ValueError(
                "mock llm_policy without a profile requires llm_mock_fallback"
            )
        return self


class AuditCreateData(BaseModel):
    audit_id: str
    status: str
    created_at: datetime


class AuditListItem(BaseModel):
    audit_id: str
    requester_id: str
    audit_mode: str
    status: str
    risk_level: str
    profile: str
    created_at: datetime
    updated_at: datetime


class AuditListData(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[AuditListItem]


class AuditStepSummary(BaseModel):
    step_id: str
    name: str
    command: str
    status: str
    exit_code: int | None
    duration_ms: int | None


class AuditStatusData(BaseModel):
    audit_id: str
    requester_id: str
    audit_mode: str
    llm_policy: str
    status: str
    profile: str
    summary: dict[str, Any]
    steps: list[AuditStepSummary]
    created_at: datetime
    updated_at: datetime


class AuditReportData(BaseModel):
    audit_id: str
    audit_mode: str
    llm_policy: str
    status: str
    passed: bool
    risk_level: str
    repo: str
    base_commit: str | None
    profile: str
    api_validation: dict[str, Any]
    code_review: dict[str, Any]
    quality: dict[str, Any]
    project_config_warnings: list[str]
    runtime: dict[str, Any]
    dependency_image: dict[str, Any]
    dependency_config: dict[str, Any] | None = None
    env_files: list[str]
    llm_adapters: list[dict[str, Any]]
    project_memory: dict[str, Any]
    recovery: dict[str, Any]
    resume: dict[str, Any]
    services: dict[str, Any]
    setup_command_count: int
    steps: list[dict[str, Any]]
    artifacts: dict[str, str]
    sandbox: dict[str, Any]
    error: dict[str, Any] | None = None
    created_at: datetime
    completed_at: datetime | None


class AuditArtifactSummary(BaseModel):
    artifact_type: str
    path: str
    metadata: dict[str, Any]
    created_at: datetime


class AuditArtifactListData(BaseModel):
    audit_id: str
    artifacts: list[AuditArtifactSummary]


class AuditArtifactContentData(BaseModel):
    audit_id: str
    artifact_type: str
    path: str
    content: str
