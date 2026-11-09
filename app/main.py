import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.v1.router import router as v1_router
from app.core.config import get_settings
from app.core.error_codes import ErrorCode
from app.core.exceptions import AuditHarnessError
from app.core.logging import (
    configure_logging,
    request_id_context,
    sanitize_for_log,
    shutdown_logging,
)
from app.db.session import init_db
from app.dependencies import recover_incomplete_audit_tasks
from app.middleware.request_logging import RequestLoggingMiddleware
from app.middleware.site_access import SiteAccessMiddleware
from app.services.runtime_readiness import sandbox_readiness

logger = logging.getLogger("audit_harness.exception")
settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.app_env.lower() in {"production", "prod"} and not settings.site_access_code:
        raise RuntimeError("APP_ACCESS_CODE is required in production")
    configure_logging(settings)
    await init_db()
    await recover_incomplete_audit_tasks()
    try:
        yield
    finally:
        shutdown_logging()


app = FastAPI(
    title="Audit Harness",
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(RequestLoggingMiddleware, settings=settings)
app.add_middleware(SiteAccessMiddleware, settings=settings)
app.include_router(v1_router)

STATIC_ROOT = Path(__file__).parent / "static"
STATIC_ROOT.mkdir(exist_ok=True)
(STATIC_ROOT / "assets").mkdir(exist_ok=True)
app.mount("/assets", StaticFiles(directory=STATIC_ROOT / "assets"), name="assets")


@app.get("/", include_in_schema=False)
async def website():
    return FileResponse(STATIC_ROOT / "index.html")


@app.get("/api/site-info", include_in_schema=False)
async def site_info():
    return {
        "app": "Audit Harness",
        "llm_model": settings.llm_model,
        "llm_configured": bool(settings.llm_api_key and settings.llm_model),
        "dependency_images_enabled": (
            settings.dependency_images_enabled and settings.app_env.lower() not in {"production", "prod"}
        ),
        **await sandbox_readiness(settings),
    }


@app.exception_handler(AuditHarnessError)
async def audit_error_handler(
    request: Request,
    exc: AuditHarnessError,
) -> JSONResponse:
    if exc.http_status >= 500:
        _log_exception(request, exc, exc.context)
    else:
        logger.warning(
            "BUSINESS_ERROR | method=%s | url=%s | code=%s "
            "| error_type=%s | detail=%s | context=%s",
            request.method,
            request.url.path,
            exc.code,
            type(exc).__name__,
            str(exc),
            sanitize_for_log(exc.context, settings.log_body_max_chars),
        )
    return JSONResponse(
        status_code=exc.http_status,
        content={
            "code": exc.code,
            "msg": exc.public_message,
            "data": None,
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    safe_errors = [
        {
            "loc": list(error.get("loc", [])),
            "msg": error.get("msg", "invalid value"),
            "type": error.get("type", "validation_error"),
        }
        for error in exc.errors()
    ]
    logger.warning(
        "REQUEST_VALIDATION_ERROR | method=%s | url=%s | errors=%s",
        request.method,
        request.url.path,
        sanitize_for_log(safe_errors, settings.log_body_max_chars),
    )
    error = ErrorCode.REQUEST_VALIDATION_ERROR
    return JSONResponse(
        status_code=error.http_status,
        content={
            "code": error.code,
            "msg": error.message,
            "data": {"errors": safe_errors},
        },
    )


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(
    request: Request,
    exc: StarletteHTTPException,
) -> JSONResponse:
    error = {
        401: ErrorCode.UNAUTHORIZED,
        403: ErrorCode.FORBIDDEN,
        404: ErrorCode.RESOURCE_NOT_FOUND,
        405: ErrorCode.METHOD_NOT_ALLOWED,
        413: ErrorCode.REQUEST_TOO_LARGE,
        429: ErrorCode.API_RATE_LIMIT,
    }.get(exc.status_code, ErrorCode.API_REQUEST_ERROR)
    logger.warning(
        "HTTP_ERROR | method=%s | url=%s | status=%s | detail=%s",
        request.method,
        request.url.path,
        exc.status_code,
        sanitize_for_log(exc.detail, settings.log_body_max_chars),
    )
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": error.code, "msg": error.message, "data": None},
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    _log_exception(request, exc)
    error = ErrorCode.SERVER_ERROR
    return JSONResponse(
        status_code=error.http_status,
        content={"code": error.code, "msg": error.message, "data": None},
    )


@app.exception_handler(SQLAlchemyError)
async def database_error_handler(
    request: Request,
    exc: SQLAlchemyError,
) -> JSONResponse:
    _log_exception(request, exc)
    error = ErrorCode.DB_ERROR
    return JSONResponse(
        status_code=error.http_status,
        content={"code": error.code, "msg": error.message, "data": None},
    )


def _log_exception(
    request: Request,
    exc: Exception,
    context: dict | None = None,
) -> None:
    logger.error(
        "SYSTEM_ERROR | method=%s | url=%s | error_type=%s "
        "| detail=%s | context=%s | request_id=%s",
        request.method,
        request.url.path,
        type(exc).__name__,
        str(exc),
        sanitize_for_log(context or {}, settings.log_body_max_chars),
        request_id_context.get(),
        exc_info=(type(exc), exc, exc.__traceback__),
    )
