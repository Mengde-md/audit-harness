from typing import Any, Literal

from pydantic import BaseModel, Field


class JsonAssertion(BaseModel):
    path: str
    operator: Literal["equals", "exists", "type"]
    expected: Any | None = None


class AcceptanceTest(BaseModel):
    id: str
    name: str
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
    path: str
    query: dict[str, Any] | None = None
    headers: dict[str, str] | None = None
    body: dict[str, Any] | None = None
    depends_on: list[str] = Field(default_factory=list)
    use: dict[str, str] = Field(default_factory=dict)
    expected_status: int
    response_mode: Literal["json", "sse"] = "json"
    assertions: list[JsonAssertion] = Field(default_factory=list)
    extract: dict[str, str] = Field(default_factory=dict)
    category: Literal["happy_path", "missing_required", "invalid_type", "chain"]
    strict: bool = True


class AcceptanceTestSuite(BaseModel):
    id: str
    tests: list[AcceptanceTest]
    warnings: list[str] = Field(default_factory=list)


class AcceptanceTestResult(BaseModel):
    test_id: str
    name: str
    passed: bool
    status_code: int | None = None
    duration_ms: int
    failures: list[str] = Field(default_factory=list)
    sse_events: list[dict[str, Any]] = Field(default_factory=list)
    extracted: dict[str, Any] = Field(default_factory=dict)
    response_excerpt: str = ""


class AcceptanceRunResult(BaseModel):
    total: int
    passed: int
    failed: int
    tests: list[AcceptanceTestResult]
