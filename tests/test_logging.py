import logging

from app.core.config import Settings
from app.core.logging import (
    configure_logging,
    sanitize_for_log,
    shutdown_logging,
)


def test_sanitize_redacts_secrets_and_truncates():
    value = {
        "api_key": "secret-value",
        "nested": {"Authorization": "Bearer abc123"},
        "content": "x" * 100,
    }

    result = sanitize_for_log(value, max_chars=80)

    assert "secret-value" not in result
    assert "abc123" not in result
    assert "***REDACTED***" in result
    assert "truncated" in result


def test_logging_writes_main_and_separate_error_files(tmp_path):
    settings = Settings(
        log_dir=tmp_path,
        log_file_name="app.log",
        error_log_file_name="error.log",
        log_rotation_mode="size",
        log_max_bytes=1024,
        log_backup_count=2,
    )
    configure_logging(settings)
    logger = logging.getLogger("test.logging")
    logger.info("BUSINESS_NODE | audit_id=audit-1")
    logger.error("SYSTEM_ERROR | error_type=Example")
    shutdown_logging()

    app_log = (tmp_path / "app.log").read_text(encoding="utf-8")
    error_log = (tmp_path / "error.log").read_text(encoding="utf-8")
    assert "BUSINESS_NODE" in app_log
    assert "SYSTEM_ERROR" in app_log
    assert "SYSTEM_ERROR" in error_log
    assert "BUSINESS_NODE" not in error_log
