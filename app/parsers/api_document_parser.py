from abc import ABC, abstractmethod

from pydantic import ValidationError

from app.core.error_codes import ErrorCode
from app.core.exceptions import ApiDocumentError, LlmError
from app.providers.llm.base import LlmProvider
from app.schemas.api_schema import ApiSchemaDocument


class ApiDocumentParser(ABC):
    @abstractmethod
    async def parse(self, markdown: str) -> ApiSchemaDocument:
        raise NotImplementedError


class LlmApiDocumentParser(ApiDocumentParser):
    def __init__(self, llm_provider: LlmProvider):
        self.llm_provider = llm_provider

    async def parse(self, markdown: str) -> ApiSchemaDocument:
        system_prompt = """
You extract an API contract from Markdown.
Return exactly one valid JSON object and nothing else.
Do not output analysis, explanations, Markdown, code fences, comments, or multiple JSON objects.
The first character of your response must be { and the last character must be }.
Never invent endpoints, fields, status codes, or business rules.
Only include endpoints with an explicit HTTP method and path.
Use this exact shape:
{
  "endpoints": [{
    "name": "string",
    "method": "GET|POST|PUT|PATCH|DELETE",
    "path": "/path",
    "request_example": {},
    "request_fields": [{
      "name": "string",
      "type": "string|integer|number|boolean|object|array|null",
      "required": true,
      "description": "string or null"
    }],
    "expected_status": 200,
    "validation_error_status": null,
    "response_mode": "json|sse",
    "expected_json": {},
    "required_paths": ["data.id"],
    "uncertain_fields": []
  }],
  "warnings": [],
  "missing_fields": []
}
Dynamic values such as IDs and timestamps belong in required_paths, not expected_json.
Set response_mode to sse when the document describes text/event-stream, SSE,
data: lines, streaming chunks, or a [DONE] marker.
When the document does not state a value, record a warning instead of guessing.
""".strip()
        try:
            raw = await self.llm_provider.generate_json(
                system_prompt=system_prompt,
                user_prompt=markdown,
            )
        except LlmError as exc:
            raise ApiDocumentError(
                "api_document_parse_failed: LLM did not return a valid API schema JSON",
                error=ErrorCode.API_DOCUMENT_ERROR,
                context={
                    "llm_error_code": exc.code,
                    "llm_error_detail": exc.detail,
                    **exc.context,
                },
            ) from exc
        try:
            schema = ApiSchemaDocument.model_validate(raw)
        except ValidationError as exc:
            raise ApiDocumentError(f"generated API schema is invalid: {exc}") from exc
        if not schema.endpoints:
            raise ApiDocumentError(
                "api_document_insufficient: no executable endpoints",
                error=ErrorCode.API_DOCUMENT_INSUFFICIENT,
            )
        return schema
