import copy
import inspect
import json
import logging
import queue
import re
import time
from collections.abc import Callable
from contextvars import ContextVar
from functools import wraps
from logging.handlers import (
    QueueHandler,
    QueueListener,
    RotatingFileHandler,
    TimedRotatingFileHandler,
)
from pathlib import Path
from typing import Any, ParamSpec, TypeVar
from uuid import uuid4

from app.core.config import Settings

P = ParamSpec("P")
R = TypeVar("R")

request_id_context: ContextVar[str] = ContextVar("request_id", default="-")
_listener: QueueListener | None = None
_configured = False

_SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "password",
    "secret",
    "token",
}


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_context.get()
        return True


class PreservingQueueHandler(QueueHandler):
    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
        prepared = copy.copy(record)
        prepared.message = prepared.getMessage()
        prepared.msg = prepared.message
        prepared.args = None
        return prepared


class ColorFormatter(logging.Formatter):
    COLORS = {
        logging.DEBUG: "\033[36m",
        logging.INFO: "\033[32m",
        logging.WARNING: "\033[33m",
        logging.ERROR: "\033[31m",
        logging.CRITICAL: "\033[35m",
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        message = super().format(record)
        color = self.COLORS.get(record.levelno, "")
        return f"{color}{message}{self.RESET}" if color else message


def configure_logging(settings: Settings) -> None:
    global _configured, _listener
    if _configured:
        return

    settings.log_dir.mkdir(parents=True, exist_ok=True)
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    log_format = (
        "%(asctime)s | %(levelname)-8s | %(name)s:%(funcName)s:%(lineno)d "
        "| request_id=%(request_id)s | %(message)s"
    )
    date_format = "%Y-%m-%d %H:%M:%S"

    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(ColorFormatter(log_format, date_format))

    app_file = _build_file_handler(
        settings.log_dir / settings.log_file_name,
        settings,
    )
    app_file.setLevel(level)
    app_file.setFormatter(logging.Formatter(log_format, date_format))

    error_file = _build_file_handler(
        settings.log_dir / settings.error_log_file_name,
        settings,
    )
    error_file.setLevel(logging.ERROR)
    error_file.setFormatter(logging.Formatter(log_format, date_format))

    log_queue: queue.Queue[logging.LogRecord] = queue.Queue(-1)
    queue_handler = PreservingQueueHandler(log_queue)
    queue_handler.setLevel(level)
    queue_handler.addFilter(RequestIdFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)
    root.addHandler(queue_handler)

    _listener = QueueListener(
        log_queue,
        console,
        app_file,
        error_file,
        respect_handler_level=True,
    )
    _listener.start()
    _configured = True
    logging.getLogger(__name__).info(
        "LOGGING_READY | dir=%s | rotation=%s | level=%s",
        settings.log_dir,
        settings.log_rotation_mode,
        settings.log_level,
    )


def shutdown_logging() -> None:
    global _configured, _listener
    if _listener is not None:
        _listener.stop()
        _listener = None
    _configured = False


def _build_file_handler(
    path: Path,
    settings: Settings,
) -> logging.Handler:
    if settings.log_rotation_mode.lower() == "time":
        return TimedRotatingFileHandler(
            path,
            when=settings.log_when,
            interval=settings.log_interval,
            backupCount=settings.log_backup_count,
            encoding="utf-8",
        )
    return RotatingFileHandler(
        path,
        maxBytes=settings.log_max_bytes,
        backupCount=settings.log_backup_count,
        encoding="utf-8",
    )


def get_logger(name: str | None = None) -> logging.Logger:
    return logging.getLogger(name or "audit_harness")


def new_request_id() -> str:
    return uuid4().hex


def set_request_id(request_id: str):
    return request_id_context.set(request_id)


def reset_request_id(token) -> None:
    request_id_context.reset(token)


def sanitize_for_log(value: Any, max_chars: int = 8_000) -> str:
    sanitized = _sanitize_value(value)
    if isinstance(sanitized, str):
        text = sanitized
    else:
        try:
            text = json.dumps(sanitized, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = repr(sanitized)
    text = _redact_inline_secrets(text)
    if len(text) > max_chars:
        return f"{text[:max_chars]}...<truncated:{len(text) - max_chars}>"
    return text


def parse_body_for_log(body: bytes, max_chars: int) -> str:
    if not body:
        return ""
    text = body.decode("utf-8", errors="replace")
    try:
        return sanitize_for_log(json.loads(text), max_chars)
    except (json.JSONDecodeError, TypeError):
        return sanitize_for_log(text, max_chars)


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "***REDACTED***"
            if str(key).lower() in _SENSITIVE_KEYS
            else _sanitize_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize_value(item) for item in value]
    return value


def _redact_inline_secrets(text: str) -> str:
    patterns = [
        r"(?i)(bearer\s+)[a-z0-9._\-]+",
        r'(?i)("?(?:api_key|apikey|password|secret|token)"?\s*[:=]\s*")([^"]+)(")',
    ]
    text = re.sub(patterns[0], r"\1***REDACTED***", text)
    return re.sub(patterns[1], r"\1***REDACTED***\3", text)


def log_api_call(
    logger: logging.Logger | None = None,
    *,
    max_chars: int = 8_000,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    target_logger = logger or get_logger("audit_harness.api")

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        if inspect.iscoroutinefunction(func):

            @wraps(func)
            async def async_wrapper(*args: P.args, **kwargs: P.kwargs):
                started = time.perf_counter()
                target_logger.info(
                    "API_REQUEST | %s | args=%s | kwargs=%s",
                    func.__name__,
                    sanitize_for_log(args, max_chars),
                    sanitize_for_log(kwargs, max_chars),
                )
                try:
                    result = await func(*args, **kwargs)
                except Exception:
                    target_logger.exception(
                        "API_ERROR | %s | cost=%.2fms",
                        func.__name__,
                        (time.perf_counter() - started) * 1000,
                    )
                    raise
                target_logger.info(
                    "API_RESPONSE | %s | cost=%.2fms | result=%s",
                    func.__name__,
                    (time.perf_counter() - started) * 1000,
                    sanitize_for_log(result, max_chars),
                )
                return result

            return async_wrapper

        @wraps(func)
        def sync_wrapper(*args: P.args, **kwargs: P.kwargs):
            started = time.perf_counter()
            target_logger.info(
                "API_REQUEST | %s | args=%s | kwargs=%s",
                func.__name__,
                sanitize_for_log(args, max_chars),
                sanitize_for_log(kwargs, max_chars),
            )
            try:
                result = func(*args, **kwargs)
            except Exception:
                target_logger.exception(
                    "API_ERROR | %s | cost=%.2fms",
                    func.__name__,
                    (time.perf_counter() - started) * 1000,
                )
                raise
            target_logger.info(
                "API_RESPONSE | %s | cost=%.2fms | result=%s",
                func.__name__,
                (time.perf_counter() - started) * 1000,
                sanitize_for_log(result, max_chars),
            )
            return result

        return sync_wrapper

    return decorator


def log_llm_request(
    *,
    model: str,
    prompt: Any,
    max_chars: int = 8_000,
) -> None:
    get_logger("audit_harness.llm").info(
        "LLM_REQUEST | model=%s | prompt=%s",
        model,
        sanitize_for_log(prompt, max_chars),
        stacklevel=2,
    )


def log_llm_response(
    *,
    model: str,
    response: Any,
    cost_ms: float,
    max_chars: int = 8_000,
) -> None:
    get_logger("audit_harness.llm").info(
        "LLM_RESPONSE | model=%s | cost=%.2fms | response=%s",
        model,
        cost_ms,
        sanitize_for_log(response, max_chars),
        stacklevel=2,
    )


def log_llm_error(
    *,
    model: str,
    cost_ms: float,
    error: Exception,
) -> None:
    get_logger("audit_harness.llm").exception(
        "LLM_ERROR | model=%s | cost=%.2fms | error_type=%s | error=%s",
        model,
        cost_ms,
        type(error).__name__,
        error,
        stacklevel=2,
    )
