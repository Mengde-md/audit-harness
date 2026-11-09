import pytest

from app.core.error_codes import ErrorCode
from app.core.exceptions import ApiDocumentError, LlmError
from app.parsers.api_document_parser import LlmApiDocumentParser
from app.providers.llm.base import LlmProvider


class FakeLlmProvider(LlmProvider):
    async def generate_json(self, *, system_prompt: str, user_prompt: str) -> dict:
        assert "Never invent" in system_prompt
        assert "GET /health" in user_prompt
        return {
            "endpoints": [
                {
                    "name": "Health",
                    "method": "GET",
                    "path": "/health",
                    "request_example": None,
                    "request_fields": [],
                    "expected_status": 200,
                    "validation_error_status": None,
                    "expected_json": {"status": "ok"},
                    "required_paths": [],
                    "uncertain_fields": [],
                }
            ],
            "warnings": ["failure response is undocumented"],
            "missing_fields": [],
        }


class InvalidJsonLlmProvider(LlmProvider):
    async def generate_json(self, *, system_prompt: str, user_prompt: str) -> dict:
        raise LlmError(
            "LLM returned invalid JSON",
            error=ErrorCode.LLM_INVALID_RESPONSE,
            context={
                "raw_response_excerpt": "I'll explain first {\"endpoints\": []}",
                "parse_error": "Extra data",
            },
        )


@pytest.mark.asyncio
async def test_parser_validates_llm_json():
    parser = LlmApiDocumentParser(FakeLlmProvider())

    schema = await parser.parse("# Health\nGET /health")

    assert schema.endpoints[0].path == "/health"
    assert schema.warnings == ["failure response is undocumented"]


@pytest.mark.asyncio
async def test_parser_wraps_invalid_llm_json_with_context():
    parser = LlmApiDocumentParser(InvalidJsonLlmProvider())

    with pytest.raises(ApiDocumentError) as exc_info:
        await parser.parse("# Health\nGET /health")

    assert exc_info.value.code == ErrorCode.API_DOCUMENT_ERROR.code
    assert exc_info.value.context["llm_error_code"] == (
        ErrorCode.LLM_INVALID_RESPONSE.code
    )
    assert "raw_response_excerpt" in exc_info.value.context
