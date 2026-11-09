from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class LlmMockMatch(BaseModel):
    contains_all: list[str] = Field(default_factory=list, max_length=20)
    contains_any: list[str] = Field(default_factory=list, max_length=20)
    stream: bool | None = None

    @model_validator(mode="after")
    def validate_matcher(self) -> "LlmMockMatch":
        if not self.contains_all and not self.contains_any:
            raise ValueError("mock rule match requires contains_all or contains_any")
        if any(not item.strip() for item in self.contains_all + self.contains_any):
            raise ValueError("mock rule keywords must not be blank")
        return self


class LlmMockResponse(BaseModel):
    format: Literal["text", "json"]
    content: Any

    @model_validator(mode="after")
    def validate_content(self) -> "LlmMockResponse":
        if self.format == "text" and not isinstance(self.content, str):
            raise ValueError("text mock response content must be a string")
        return self


class LlmMockRule(BaseModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")
    priority: int = Field(default=0, ge=-1000, le=1000)
    match: LlmMockMatch
    response: LlmMockResponse


class LlmMockConfig(BaseModel):
    version: Literal[1] = 1
    profile: Literal["generic-agent"] | None = "generic-agent"
    rules: list[LlmMockRule] = Field(default_factory=list, max_length=100)
    fallback: LlmMockResponse | None = None
