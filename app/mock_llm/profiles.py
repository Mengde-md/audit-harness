from app.schemas.mock_llm import (
    LlmMockMatch,
    LlmMockResponse,
    LlmMockRule,
)


def load_profile(name: str | None) -> tuple[list[LlmMockRule], LlmMockResponse | None]:
    if name is None:
        return [], None
    if name != "generic-agent":
        raise ValueError(f"unsupported mock LLM profile: {name}")
    return _generic_agent_rules(), LlmMockResponse(
        format="text",
        content="Mock LLM response for offline audit.",
    )


def _generic_agent_rules() -> list[LlmMockRule]:
    return [
        LlmMockRule(
            id="generic_prd_review",
            priority=100,
            match=LlmMockMatch(
                contains_all=["passed", "issues"],
                contains_any=["review", "审核", "评审"],
            ),
            response=LlmMockResponse(
                format="json",
                content={
                    "passed": True,
                    "summary": "Mock audit review passed.",
                    "issues": [],
                },
            ),
        ),
        LlmMockRule(
            id="generic_structured_summary",
            priority=90,
            match=LlmMockMatch(
                contains_all=["json"],
                contains_any=["summary", "摘要", "同步"],
            ),
            response=LlmMockResponse(
                format="json",
                content={
                    "summary": "Mock summary generated for offline audit.",
                    "key_points": ["Deterministic mock response"],
                },
            ),
        ),
        LlmMockRule(
            id="generic_api_document",
            priority=80,
            match=LlmMockMatch(
                contains_any=["api document", "api docs", "接口文档"]
            ),
            response=LlmMockResponse(
                format="text",
                content=(
                    "# API Documentation\n\n"
                    "## GET /health\n\nReturns the service health status.\n"
                ),
            ),
        ),
        LlmMockRule(
            id="generic_prompt_suite",
            priority=70,
            match=LlmMockMatch(contains_any=["prompt", "提示词"]),
            response=LlmMockResponse(
                format="text",
                content=(
                    "# Prompt Suite\n\n## System Prompt\n\n"
                    "Complete the requested task with structured output.\n"
                ),
            ),
        ),
        LlmMockRule(
            id="generic_document_optimization",
            priority=60,
            match=LlmMockMatch(
                contains_any=["optimize", "优化", "rewrite", "改写"]
            ),
            response=LlmMockResponse(
                format="text",
                content=(
                    "# Optimized Document\n\n"
                    "This deterministic response validates the "
                    "optimization workflow.\n"
                ),
            ),
        ),
        LlmMockRule(
            id="generic_prd_writer",
            priority=50,
            match=LlmMockMatch(
                contains_any=["prd", "product requirement", "产品需求"]
            ),
            response=LlmMockResponse(
                format="text",
                content=(
                    "# Product Requirements Document\n\n"
                    "## Goal\n\n"
                    "Validate the product workflow in an offline audit.\n\n"
                    "## Acceptance Criteria\n\n"
                    "- The service returns a deterministic result.\n"
                ),
            ),
        ),
    ]
