from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        populate_by_name=True,
    )

    app_env: str = "local"
    site_access_code: str = Field(default="", alias="APP_ACCESS_CODE")
    site_session_secret: str = Field(default="", alias="APP_SESSION_SECRET")
    max_pending_audits: int = Field(default=10, ge=1, le=100)
    database_url: str = (
        f"sqlite+aiosqlite:///{PROJECT_ROOT / 'tmp' / 'audit_harness.db'}"
    )
    workspace_root: Path = PROJECT_ROOT / "tmp" / "audits"
    test_suite_cache_root: Path = PROJECT_ROOT / "tmp" / "cache" / "test-suites"
    recovery_memory_root: Path = PROJECT_ROOT / "tmp" / "cache" / "recovery"
    project_recovery_high_success_count: int = 3
    cross_recovery_deposit_repo_count: int = 3
    cross_recovery_deposit_success_count: int = 5
    cross_recovery_middle_repo_count: int = 3
    cross_recovery_middle_success_count: int = 10
    cross_recovery_high_repo_count: int = 5
    cross_recovery_high_success_count: int = 20
    recovery_base_retry_count: int = 1
    recovery_high_extra_retry_count: int = 1
    recovery_hard_max_attempts: int = 2
    default_timeout_seconds: int = 300
    service_startup_timeout_seconds: int = 30
    max_log_chars: int = 12_000
    max_patch_chars: int = 200_000
    max_api_document_chars: int = 200_000
    enable_background_tasks: bool = True

    log_level: str = "INFO"
    log_dir: Path = Path("logs")
    log_file_name: str = "app.log"
    error_log_file_name: str = "error.log"
    log_rotation_mode: str = "size"
    log_max_bytes: int = 20 * 1024 * 1024
    log_backup_count: int = 10
    log_when: str = "midnight"
    log_interval: int = 1
    log_request_body: bool = False
    log_response_body: bool = False
    log_llm_content: bool = False
    log_body_max_chars: int = 8_000

    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = ""
    llm_timeout_seconds: int = 60
    llm_temperature: float = 0

    sandbox_backend: str = "docker"
    sandbox_network: str = "none"
    sandbox_memory: str = "1g"
    sandbox_cpus: float = 1
    sandbox_user: str = "1000:1000"
    docker_api_timeout_seconds: int = 600
    mock_llm_port: int = 9001
    dependency_images_enabled: bool = True
    dependency_build_timeout_seconds: int = 900
    dependency_pip_index_url: str = "https://mirrors.aliyun.com/pypi/simple/"
    python_sandbox_image: str = "audit-harness/python-basic:3.11"
    python_runtime_images_raw: str = Field(
        default=(
            "3.10=audit-harness/python-basic:3.10,"
            "3.11=audit-harness/python-basic:3.11,"
            "3.12=audit-harness/python-basic:3.12"
        ),
        alias="PYTHON_RUNTIME_IMAGES",
    )
    sandbox_allowed_images_raw: str = Field(
        default=(
            "audit-harness/python-basic:3.10,"
            "audit-harness/python-basic:3.11,"
            "audit-harness/python-basic:3.12"
        ),
        alias="SANDBOX_ALLOWED_IMAGES",
    )

    @property
    def sandbox_allowed_images(self) -> set[str]:
        return {
            item.strip()
            for item in self.sandbox_allowed_images_raw.split(",")
            if item.strip()
        }

    @property
    def python_runtime_images(self) -> dict[str, str]:
        images: dict[str, str] = {}
        for item in self.python_runtime_images_raw.split(","):
            if not item.strip():
                continue
            version, _, image = item.partition("=")
            if version.strip() and image.strip():
                images[version.strip()] = image.strip()
        return images

    def ensure_runtime_dirs(self) -> None:
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.test_suite_cache_root.mkdir(parents=True, exist_ok=True)
        self.recovery_memory_root.mkdir(parents=True, exist_ok=True)
        if self.database_url.startswith("sqlite"):
            database_path = self.database_url.rsplit("///", 1)[-1]
            if database_path and database_path != ":memory:":
                Path(database_path).expanduser().parent.mkdir(
                    parents=True,
                    exist_ok=True,
                )


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_runtime_dirs()
    return settings
