import json

import pytest

from app.api_testing.schema_validator import ApiSchemaValidator
from app.api_testing.test_suite_freezer import TestSuiteFreezer
from app.generators.acceptance_test_generator import SchemaAcceptanceTestGenerator
from app.schemas.api_schema import ApiEndpoint, ApiField, ApiSchemaDocument


@pytest.mark.asyncio
async def test_generates_only_documented_negative_tests(tmp_path):
    schema = ApiSchemaDocument(
        endpoints=[
            ApiEndpoint(
                name="Create item",
                method="POST",
                path="/items",
                request_example={"name": "demo", "count": 1},
                request_fields=[
                    ApiField(name="name", type="string", required=True),
                    ApiField(name="count", type="integer", required=True),
                ],
                expected_status=200,
                validation_error_status=422,
                expected_json={"code": 0},
                required_paths=["data.id"],
            )
        ]
    )

    validated = ApiSchemaValidator().validate(schema)
    suite = await SchemaAcceptanceTestGenerator().generate(validated)

    assert len(suite.tests) == 5
    assert suite.tests[0].category == "happy_path"
    assert {test.category for test in suite.tests[1:]} == {
        "missing_required",
        "invalid_type",
    }
    happy_assertions = {
        (item.path, item.operator, item.expected)
        for item in suite.tests[0].assertions
    }
    assert ("code", "type", "integer") in happy_assertions
    assert ("data.id", "exists", None) in happy_assertions
    assert all(item.operator != "equals" for item in suite.tests[0].assertions)

    schema_path, suite_path, digest = TestSuiteFreezer().freeze(
        input_dir=tmp_path,
        schema=schema,
        suite=suite,
    )
    assert schema_path.exists()
    assert suite_path.exists()
    assert digest.startswith("sha256:")
    assert json.loads(suite_path.read_text())["id"] == suite.id


@pytest.mark.asyncio
async def test_does_not_guess_validation_status():
    schema = ApiSchemaDocument(
        endpoints=[
            ApiEndpoint(
                name="Health",
                method="GET",
                path="/health",
                expected_status=200,
                expected_json={"status": "ok"},
            )
        ]
    )

    suite = await SchemaAcceptanceTestGenerator().generate(schema)

    assert len(suite.tests) == 1
    assert suite.tests[0].category == "happy_path"
    assert suite.tests[0].assertions[0].operator == "type"
    assert suite.tests[0].assertions[0].expected == "string"


@pytest.mark.asyncio
async def test_deduplicates_equivalent_tests():
    endpoint = ApiEndpoint(
        name="Health",
        method="GET",
        path="/health",
        expected_status=200,
        expected_json={"status": "ok"},
    )
    schema = ApiSchemaDocument(endpoints=[endpoint, endpoint.model_copy()])

    suite = await SchemaAcceptanceTestGenerator().generate(schema)

    assert len(suite.tests) == 1
