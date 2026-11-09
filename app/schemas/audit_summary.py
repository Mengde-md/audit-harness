from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DependencyImageSummary(BaseModel):
    image: str = ""
    dependency_file: str | None = None
    fingerprint: str = ""
    cache_hit: bool | None = None


class ApiValidationSummary(BaseModel):
    schema_id: str | None = None
    test_suite_id: str | None = None
    test_suite_hash: str | None = None
    generated_tests: int = 0
    passed_tests: int = 0
    failed_tests: int = 0
    warnings: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    test_suite_source: str | None = None
    cache_key: str | None = None
    cache_hit: bool | None = None
    results: list[dict[str, Any]] = Field(default_factory=list)


class ProjectMemorySummary(BaseModel):
    loaded_files: list[str] = Field(default_factory=list)
    known_issue_count: int = 0
    warnings: list[str] = Field(default_factory=list)


class RuntimeSummary(BaseModel):
    language: str = "python"
    version: str = "3.11"
    image: str = ""
    source: str = "default"


class AuditSummary(BaseModel):
    model_config = ConfigDict(extra="allow")

    passed: bool | None = None
    total_steps: int = 0
    passed_steps: int = 0
    failed_steps: int = 0
    api_validation: ApiValidationSummary = Field(default_factory=ApiValidationSummary)
    dependency_image: DependencyImageSummary = Field(
        default_factory=DependencyImageSummary
    )
    llm_policy: str = "unknown"
    llm_mock_profile: str | None = None
    llm_mock_rule_count: int = 0
    project_config_warnings: list[str] = Field(default_factory=list)
    runtime: RuntimeSummary = Field(default_factory=RuntimeSummary)
    dependency_config: dict[str, Any] | None = None
    env_files: list[str] = Field(default_factory=list)
    llm_adapters: list[dict[str, Any]] = Field(default_factory=list)
    services: dict[str, Any] = Field(default_factory=dict)
    setup_command_count: int = 0
    project_memory: ProjectMemorySummary = Field(default_factory=ProjectMemorySummary)
    recovery: dict[str, Any] = Field(default_factory=dict)
    code_review: dict[str, Any] = Field(default_factory=dict)
    error_code: int | str | None = None

    def to_json_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)
