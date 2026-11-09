from app.api_testing.json_assertions import JsonAssertionEngine
from app.api_testing.json_tools import get_json_path
from app.schemas.acceptance_test import AcceptanceTest, JsonAssertion


def make_test(assertions: list[JsonAssertion]) -> AcceptanceTest:
    return AcceptanceTest(
        id="rag-happy-path",
        name="生成接口文档前检索 RAG 参考资料 - happy path",
        method="POST",
        path="/api/retrieve-api-docs-rag",
        expected_status=200,
        assertions=assertions,
        category="happy_path",
    )


def test_get_json_path_supports_array_wildcard() -> None:
    payload = {
        "hits": [
            {"source": "history-copywriting-api.md", "score": 0.5},
            {"source": "team-api-guidelines.md", "score": 0.18},
        ]
    }

    found, value = get_json_path(payload, "hits[].source")

    assert found is True
    assert value == ["history-copywriting-api.md", "team-api-guidelines.md"]


def test_json_assertions_accept_array_wildcard_paths() -> None:
    payload = {
        "status": "completed",
        "hits": [
            {
                "source": "history-copywriting-api.md",
                "title": "1. 生成文案",
                "score": 0.5,
                "content": "full markdown",
                "content_preview": "preview",
            },
            {
                "source": "team-api-guidelines.md",
                "title": "团队接口设计规范",
                "score": 0.18,
                "content": "guideline",
                "content_preview": "guideline preview",
            },
        ],
    }
    test = make_test(
        [
            JsonAssertion(path="hits[].source", operator="exists"),
            JsonAssertion(path="hits[].title", operator="type", expected="string"),
            JsonAssertion(path="hits[].score", operator="type", expected="number"),
            JsonAssertion(path="hits[].content_preview", operator="exists"),
            JsonAssertion(path="hits[].content", operator="exists"),
        ]
    )

    failures = JsonAssertionEngine().assert_json(payload, test)

    assert failures == []


def test_json_assertions_fail_when_any_array_item_misses_wildcard_path() -> None:
    payload = {
        "hits": [
            {"source": "history-copywriting-api.md"},
            {"title": "团队接口设计规范"},
        ]
    }
    test = make_test([JsonAssertion(path="hits[].source", operator="exists")])

    failures = JsonAssertionEngine().assert_json(payload, test)

    assert failures == ["missing JSON path: hits[].source"]


def test_get_json_path_supports_bracket_index() -> None:
    payload = {"hits": [{"source": "history-copywriting-api.md"}]}

    found, value = get_json_path(payload, "hits[0].source")

    assert found is True
    assert value == "history-copywriting-api.md"
