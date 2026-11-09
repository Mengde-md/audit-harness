from typing import Any, Literal

from pydantic import BaseModel, Field


class ApiField(BaseModel):
    name: str
    type: str | None = None
    required: bool = False
    description: str | None = None


class ApiEndpoint(BaseModel):
    name: str
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
    path: str
    request_example: dict[str, Any] | None = None
    request_fields: list[ApiField] = Field(default_factory=list)
    expected_status: int = 200
    validation_error_status: int | None = None
    response_mode: Literal["json", "sse"] = "json"
    expected_json: dict[str, Any] = Field(default_factory=dict)
    required_paths: list[str] = Field(default_factory=list)
    uncertain_fields: list[str] = Field(default_factory=list)


class ApiSchemaDocument(BaseModel):
    endpoints: list[ApiEndpoint]
    warnings: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
