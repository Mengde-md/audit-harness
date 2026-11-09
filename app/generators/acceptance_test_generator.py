"""Generate executable acceptance tests from a normalized API schema."""

import json
from abc import ABC, abstractmethod
from copy import deepcopy
from typing import Any

from app.schemas.acceptance_test import (
    AcceptanceTest,
    AcceptanceTestSuite,
    JsonAssertion,
)
from app.schemas.api_schema import ApiEndpoint, ApiSchemaDocument
from app.utils.id_generator import generate_id


class AcceptanceTestGenerator(ABC):
    """Interface for schema-to-acceptance-suite generators."""

    @abstractmethod
    async def generate(self, api_schema: ApiSchemaDocument) -> AcceptanceTestSuite:
        """Build an executable suite from parsed API documentation."""
        raise NotImplementedError


class SchemaAcceptanceTestGenerator(AcceptanceTestGenerator):
    """Deterministic generator used when project memory does not provide a suite."""

    async def generate(self, api_schema: ApiSchemaDocument) -> AcceptanceTestSuite:
        """Create happy-path tests plus documented validation-error tests."""
        happy_tests: list[AcceptanceTest] = []
        negative_tests: list[AcceptanceTest] = []
        for endpoint in api_schema.endpoints:
            happy_tests.append(self._happy_path(endpoint))
            if endpoint.validation_error_status is not None:
                negative_tests.extend(self._negative_tests(endpoint))
        tests = [*self._link_chain_variables(happy_tests), *negative_tests]

        return AcceptanceTestSuite(
            id=generate_id(),
            tests=self._deduplicate(tests),
            warnings=api_schema.warnings,
        )

    def _happy_path(self, endpoint: ApiEndpoint) -> AcceptanceTest:
        """Create the main contract test for one endpoint."""
        assertions = [
            JsonAssertion(path=path, operator="exists")
            for path in endpoint.required_paths
        ]
        assertions.extend(self._structure_assertions(endpoint.expected_json))
        return AcceptanceTest(
            id=generate_id(),
            name=f"{endpoint.name} - happy path",
            method=endpoint.method,
            path=endpoint.path,
            body=endpoint.request_example,
            expected_status=endpoint.expected_status,
            response_mode=endpoint.response_mode,
            assertions=assertions,
            extract=self._extractable_ids(endpoint),
            category="happy_path",
        )

    def _negative_tests(self, endpoint: ApiEndpoint) -> list[AcceptanceTest]:
        """Generate negative tests only when the API doc declares validation status."""
        if endpoint.request_example is None:
            return []
        tests: list[AcceptanceTest] = []
        for field in endpoint.request_fields:
            if field.required and field.name in endpoint.request_example:
                body = deepcopy(endpoint.request_example)
                body.pop(field.name)
                tests.append(
                    AcceptanceTest(
                        id=generate_id(),
                        name=f"{endpoint.name} - missing {field.name}",
                        method=endpoint.method,
                        path=endpoint.path,
                        body=body,
                        expected_status=endpoint.validation_error_status or 400,
                        response_mode="json",
                        category="missing_required",
                    )
                )
            if field.type and field.name in endpoint.request_example:
                body = deepcopy(endpoint.request_example)
                body[field.name] = self._invalid_value(field.type)
                tests.append(
                    AcceptanceTest(
                        id=generate_id(),
                        name=f"{endpoint.name} - invalid type for {field.name}",
                        method=endpoint.method,
                        path=endpoint.path,
                        body=body,
                        expected_status=endpoint.validation_error_status or 400,
                        response_mode="json",
                        category="invalid_type",
                    )
                )
        return tests

    def _structure_assertions(
        self,
        data: dict[str, Any],
        prefix: str = "",
    ) -> list[JsonAssertion]:
        """Assert response shape/types instead of brittle exact example values."""
        assertions: list[JsonAssertion] = []
        for key, value in data.items():
            path = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict):
                assertions.append(
                    JsonAssertion(path=path, operator="type", expected="object")
                )
                assertions.extend(self._structure_assertions(value, path))
            else:
                assertions.append(
                    JsonAssertion(
                        path=path,
                        operator="type",
                        expected=self._json_type(value),
                    )
                )
        return assertions

    def _deduplicate(self, tests: list[AcceptanceTest]) -> list[AcceptanceTest]:
        """Remove equivalent generated tests while preserving meaningful DAG fields."""
        deduplicated: list[AcceptanceTest] = []
        seen: set[str] = set()
        for test in tests:
            key = json.dumps(
                {
                    "method": test.method,
                    "path": test.path,
                    "query": test.query,
                    "headers": test.headers,
                    "category": test.category,
                    "depends_on": test.depends_on,
                    "use": test.use,
                    "body": test.body,
                    "extract": test.extract,
                    "expected_status": test.expected_status,
                    "response_mode": test.response_mode,
                },
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
            if key not in seen:
                seen.add(key)
                deduplicated.append(test)
        return deduplicated

    @staticmethod
    def _json_type(value: Any) -> str:
        """Map Python sample values to JSON type assertion names."""
        if value is None:
            return "null"
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, str):
            return "string"
        if isinstance(value, int):
            return "integer"
        if isinstance(value, float):
            return "number"
        if isinstance(value, list):
            return "array"
        return "object"

    @staticmethod
    def _invalid_value(field_type: str) -> Any:
        """Return a simple invalid value for documented request field types."""
        values = {
            "string": 12345,
            "integer": "not-an-integer",
            "number": "not-a-number",
            "boolean": "not-a-boolean",
            "object": [],
            "array": {},
        }
        return values.get(field_type, {"invalid": True})

    def _link_chain_variables(
        self,
        tests: list[AcceptanceTest],
    ) -> list[AcceptanceTest]:
        """Link later tests to extracted ids from earlier happy-path tests.

        This is a conservative heuristic for common REST workflows. Explicit
        project-provided suites can still override it with richer ``depends_on``
        and ``use`` instructions.
        """
        linked: list[AcceptanceTest] = []
        available: dict[str, str] = {}
        for test in tests:
            body, body_dependencies = self._replace_known_variables(
                test.body,
                available,
            )
            path, path_dependencies = self._replace_path_parameters(
                test.path,
                available,
            )
            dependencies = sorted(body_dependencies | path_dependencies)
            used_chain = body != test.body or path != test.path
            # The category is upgraded to "chain" so reports can distinguish
            # standalone endpoint checks from multi-call API flows.
            linked.append(
                test.model_copy(
                    update={
                        "body": body,
                        "path": path,
                        "depends_on": dependencies,
                        "category": "chain" if used_chain else test.category,
                    }
                )
            )
            for name in test.extract:
                available.setdefault(name, test.id)
        return linked

    def _replace_known_variables(
        self,
        value: Any,
        available: dict[str, str],
    ) -> tuple[Any, set[str]]:
        """Replace known identifier fields with scoped template variables."""
        if isinstance(value, dict):
            rendered: dict[str, Any] = {}
            dependencies: set[str] = set()
            for key, item in value.items():
                if key in available and self._looks_like_identifier_field(key):
                    # Scoped variables avoid collisions when multiple previous
                    # endpoints expose the same generic field name like "id".
                    rendered[key] = f"{{{{{available[key]}.{key}}}}}"
                    dependencies.add(available[key])
                    continue
                rendered_item, item_dependencies = self._replace_known_variables(
                    item,
                    available,
                )
                rendered[key] = rendered_item
                dependencies.update(item_dependencies)
            return rendered, dependencies
        if isinstance(value, list):
            rendered_items: list[Any] = []
            dependencies: set[str] = set()
            for item in value:
                rendered_item, item_dependencies = self._replace_known_variables(
                    item,
                    available,
                )
                rendered_items.append(rendered_item)
                dependencies.update(item_dependencies)
            return rendered_items, dependencies
        return value, set()

    def _replace_path_parameters(
        self,
        path: str,
        available: dict[str, str],
    ) -> tuple[str, set[str]]:
        """Replace path placeholders like ``{kb_id}`` or ``:kb_id``."""
        rendered = path
        dependencies: set[str] = set()
        for name, producer_id in sorted(
            available.items(),
            key=lambda item: len(item[0]),
            reverse=True,
        ):
            scoped = f"{{{{{producer_id}.{name}}}}}"
            next_rendered = rendered.replace(f"{{{name}}}", scoped)
            next_rendered = next_rendered.replace(f":{name}", scoped)
            if next_rendered != rendered:
                dependencies.add(producer_id)
            rendered = next_rendered
        return rendered, dependencies

    def _extractable_ids(self, endpoint: ApiEndpoint) -> dict[str, str]:
        """Extract likely resource identifiers for downstream test chaining."""
        extracts: dict[str, str] = {}
        for path in [*endpoint.required_paths, *self._json_paths(endpoint.expected_json)]:
            name = path.split(".")[-1]
            if self._looks_like_identifier_field(name):
                extracts.setdefault(name, f"$.{path}")
        return extracts

    def _json_paths(self, data: dict[str, Any], prefix: str = "") -> list[str]:
        """Flatten a JSON example into dot-separated paths."""
        paths: list[str] = []
        for key, value in data.items():
            path = f"{prefix}.{key}" if prefix else key
            paths.append(path)
            if isinstance(value, dict):
                paths.extend(self._json_paths(value, path))
        return paths

    @staticmethod
    def _looks_like_identifier_field(name: str) -> bool:
        """Identify fields that are safe candidates for API data chaining."""
        return name == "id" or name.endswith("_id") or name.endswith("Id")
