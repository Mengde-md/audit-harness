from typing import Any, NoReturn

from app.core.error_codes import ErrorCode


class AuditHarnessError(Exception):
    default_error = ErrorCode.SERVER_ERROR

    def __init__(
        self,
        detail: str | None = None,
        *,
        error: ErrorCode | None = None,
        context: dict[str, Any] | None = None,
    ):
        self.error = error or self.default_error
        self.code = self.error.code
        self.public_message = self.error.message
        self.http_status = self.error.http_status
        self.detail = detail
        self.context = context or {}
        super().__init__(detail or self.public_message)


class AuditNotFoundError(AuditHarnessError):
    default_error = ErrorCode.RESOURCE_NOT_FOUND


class InvalidInputError(AuditHarnessError):
    default_error = ErrorCode.PARAM_ERROR


class WorkspaceError(AuditHarnessError):
    default_error = ErrorCode.WORKSPACE_ERROR


class SandboxError(AuditHarnessError):
    default_error = ErrorCode.SANDBOX_ERROR


class ApiDocumentError(AuditHarnessError):
    default_error = ErrorCode.API_DOCUMENT_ERROR


class LlmError(AuditHarnessError):
    default_error = ErrorCode.LLM_ERROR


class DatabaseError(AuditHarnessError):
    default_error = ErrorCode.DB_ERROR


def raise_error(
    error: ErrorCode,
    *,
    detail: str | None = None,
    context: dict[str, Any] | None = None,
) -> NoReturn:
    raise AuditHarnessError(detail, error=error, context=context)
