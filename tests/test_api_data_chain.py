import json

import httpx
import pytest

from app.api_testing.http_acceptance_runner import HttpAcceptanceRunner
from app.generators.acceptance_test_generator import SchemaAcceptanceTestGenerator
from app.schemas.acceptance_test import AcceptanceTest, AcceptanceTestSuite
from app.schemas.api_schema import ApiEndpoint, ApiSchemaDocument


@pytest.mark.asyncio
async def test_runner_extracts_and_injects_chain_variables(monkeypatch):
    seen_upload_body = {}

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_upload_body
        if request.url.path == "/knowledge-bases":
            return httpx.Response(
                200,
                json={"code": 0, "data": {"kb_id": "kb-real"}},
            )
        if request.url.path == "/documents":
            seen_upload_body = json.loads(request.content.decode())
            return httpx.Response(200, json={"code": 0})
        return httpx.Response(404, json={"code": 404})

    transport = httpx.MockTransport(handler)
    original_async_client = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return original_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client_factory)

    suite = AcceptanceTestSuite(
        id="suite-1",
        tests=[
            AcceptanceTest(
                id="test-create",
                name="create kb",
                method="POST",
                path="/knowledge-bases",
                body={"name": "demo"},
                expected_status=200,
                extract={"kb_id": "$.data.kb_id"},
                category="happy_path",
            ),
            AcceptanceTest(
                id="test-upload",
                name="upload document",
                method="POST",
                path="/documents",
                depends_on=["test-create"],
                body={"kb_id": "{{test-create.kb_id}}", "content": "hello"},
                expected_status=200,
                category="chain",
            ),
        ],
    )

    result = await HttpAcceptanceRunner().run(suite, "http://testserver")

    assert result.failed == 0
    assert result.tests[0].extracted == {"kb_id": "kb-real"}
    assert seen_upload_body["kb_id"] == "kb-real"


@pytest.mark.asyncio
async def test_runner_fails_fast_when_dependency_did_not_pass():
    suite = AcceptanceTestSuite(
        id="suite-1",
        tests=[
            AcceptanceTest(
                id="test-upload",
                name="upload document",
                method="POST",
                path="/documents",
                depends_on=["test-create"],
                body={"kb_id": "{{test-create.kb_id}}"},
                expected_status=200,
                category="chain",
            )
        ],
    )

    result = await HttpAcceptanceRunner().run(suite, "http://testserver")

    assert result.failed == 1
    assert result.tests[0].failures == ["missing dependency: test-create"]


@pytest.mark.asyncio
async def test_runner_marks_downstream_blocked_when_dependency_fails(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/create":
            return httpx.Response(500, json={"code": 500})
        return httpx.Response(200, json={"code": 0})

    transport = httpx.MockTransport(handler)
    original_async_client = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return original_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client_factory)

    suite = AcceptanceTestSuite(
        id="suite-1",
        tests=[
            AcceptanceTest(
                id="create",
                name="create",
                method="POST",
                path="/create",
                expected_status=200,
                category="happy_path",
            ),
            AcceptanceTest(
                id="use",
                name="use",
                method="POST",
                path="/use",
                depends_on=["create"],
                expected_status=200,
                category="chain",
            ),
        ],
    )

    result = await HttpAcceptanceRunner().run(suite, "http://testserver")

    assert result.failed == 2
    assert result.tests[0].failures == ["expected status 200, got 500"]
    assert result.tests[1].failures == ["blocked dependency: create"]


@pytest.mark.asyncio
async def test_runner_detects_cyclic_dependencies():
    suite = AcceptanceTestSuite(
        id="suite-1",
        tests=[
            AcceptanceTest(
                id="a",
                name="a",
                method="GET",
                path="/a",
                depends_on=["b"],
                expected_status=200,
                category="chain",
            ),
            AcceptanceTest(
                id="b",
                name="b",
                method="GET",
                path="/b",
                depends_on=["a"],
                expected_status=200,
                category="chain",
            ),
        ],
    )

    result = await HttpAcceptanceRunner().run(suite, "http://testserver")

    assert result.failed == 2
    assert {item.failures[0] for item in result.tests} == {
        "cyclic dependency detected"
    }


def test_renderer_injects_explicit_use_targets():
    test = AcceptanceTest(
        id="test-upload",
        name="upload document",
        method="POST",
        path="/documents",
        use={"body.kb_id": "{{test-create.kb_id}}"},
        body={"content": "hello"},
        expected_status=200,
        category="chain",
    )

    rendered, failures = HttpAcceptanceRunner().renderer.render(
        test,
        {"test-create.kb_id": "kb-real"},
    )

    assert failures == []
    assert rendered["body"] == {"content": "hello", "kb_id": "kb-real"}


@pytest.mark.asyncio
async def test_runner_fails_fast_when_chain_variable_is_missing():
    test = AcceptanceTest(
        id="test-upload",
        name="upload document",
        method="POST",
        path="/documents",
        body={"kb_id": "{{kb_id}}"},
        expected_status=200,
        category="chain",
    )

    async with httpx.AsyncClient(base_url="http://testserver") as client:
        result = await HttpAcceptanceRunner()._run_one(client, test, {})

    assert result.passed is False
    assert result.failures == ["missing variable: kb_id"]
    assert result.status_code is None


@pytest.mark.asyncio
async def test_generator_links_resource_id_between_happy_path_tests():
    schema = ApiSchemaDocument(
        endpoints=[
            ApiEndpoint(
                name="Create knowledge base",
                method="POST",
                path="/knowledge-bases",
                request_example={"name": "demo"},
                expected_status=200,
                expected_json={"code": 0, "data": {"kb_id": "kb-demo"}},
                required_paths=["data.kb_id"],
            ),
            ApiEndpoint(
                name="Upload document",
                method="POST",
                path="/documents",
                request_example={"kb_id": "kb_xxx", "content": "hello"},
                expected_status=200,
                expected_json={"code": 0},
            ),
        ],
    )

    suite = await SchemaAcceptanceTestGenerator().generate(schema)

    assert suite.tests[0].extract == {"kb_id": "$.data.kb_id"}
    assert suite.tests[1].category == "chain"
    assert suite.tests[1].depends_on == [suite.tests[0].id]
    assert suite.tests[1].body["kb_id"] == f"{{{{{suite.tests[0].id}.kb_id}}}}"
