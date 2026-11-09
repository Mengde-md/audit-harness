from typing import Any

from app.api_testing.json_tools import (
    get_json_path,
    get_json_path_values,
    value_matches_type,
)
from app.schemas.acceptance_test import AcceptanceTest


class JsonAssertionEngine:
    def assert_json(self, payload: Any, test: AcceptanceTest) -> list[str]:
        failures: list[str] = []
        for assertion in test.assertions:
            found, values = get_json_path_values(payload, assertion.path)
            value = values if len(values) != 1 else values[0]
            if assertion.operator == "exists" and not found:
                failures.append(f"missing JSON path: {assertion.path}")
            elif assertion.operator == "equals":
                if not found or not all(item == assertion.expected for item in values):
                    failures.append(
                        f"{assertion.path}: expected {assertion.expected!r}, got {value!r}"
                    )
            elif assertion.operator == "type":
                if not found or not all(
                    value_matches_type(item, str(assertion.expected))
                    for item in values
                ):
                    failures.append(
                        f"{assertion.path}: expected type {assertion.expected}"
                    )
        return failures


class JsonExtractor:
    def extract(
        self,
        payload: Any,
        test: AcceptanceTest,
    ) -> tuple[dict[str, Any], list[str]]:
        extracted: dict[str, Any] = {}
        failures: list[str] = []
        for name, path in test.extract.items():
            found, value = get_json_path(payload, path)
            if not found:
                failures.append(f"missing extract path: {name}={path}")
                continue
            extracted[name] = value
        return extracted, failures
