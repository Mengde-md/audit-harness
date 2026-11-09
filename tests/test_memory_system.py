import json

import pytest

from app.api_testing.schema_validator import ApiSchemaValidator
from app.api_testing.test_suite_freezer import TestSuiteFreezer
from app.generators.acceptance_test_generator import SchemaAcceptanceTestGenerator
from app.memory.project_memory import ProjectMemoryLoader
from app.memory.task_memory import TaskMemoryWriter
from app.memory.test_suite_cache import LlmTestSuiteCache
from app.parsers.api_document_parser import ApiDocumentParser
from app.schemas.api_schema import ApiEndpoint, ApiSchemaDocument
from app.services.api_test_service import ApiTestService


class CountingParser(ApiDocumentParser):
    def __init__(self):
        self.calls = 0

    async def parse(self, markdown: str) -> ApiSchemaDocument:
        self.calls += 1
        return ApiSchemaDocument(
            endpoints=[
                ApiEndpoint(
                    name="Health",
                    method="GET",
                    path="/health",
                    expected_json={"status": "ok"},
                )
            ]
        )


def test_project_memory_loader_reads_supported_files(tmp_path):
    memory_root = tmp_path / ".audit-harness"
    (memory_root / "tests").mkdir(parents=True)
    (memory_root / "PROJECT.md").write_text("# Project\nFastAPI", encoding="utf-8")
    (memory_root / "KNOWN_ISSUES.md").write_text(
        "# Known Issues\n\n## KI-001 Existing mismatch\n",
        encoding="utf-8",
    )
    (memory_root / "MOCK_RULES.json").write_text(
        json.dumps(
            {
                "rules": [
                    {
                        "id": "project_rule",
                        "match": {"contains_any": ["hello"]},
                        "response": {"format": "text", "content": "world"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    memory = ProjectMemoryLoader().load(tmp_path)

    assert memory.loaded_files == [
        "PROJECT.md",
        "KNOWN_ISSUES.md",
        "MOCK_RULES.json",
    ]
    assert memory.known_issue_count == 1
    assert memory.mock_rules[0].id == "project_rule"


def test_task_memory_uses_unified_memory_envelope(tmp_path):
    writer = TaskMemoryWriter(tmp_path)

    writer.append("step_finished", audit_id="audit-1", status="passed")

    payload = json.loads(writer.path.read_text(encoding="utf-8"))
    assert payload["event"] == "step_finished"
    assert payload["scope"] == "single_audit"
    assert payload["source"] == "system"
    assert payload["trust_level"] == "LOW"
    assert payload["memory_id"]
    assert payload["content"] == {
        "event": "step_finished",
        "audit_id": "audit-1",
        "status": "passed",
    }


@pytest.mark.asyncio
async def test_generated_test_suite_is_reused_from_cache(tmp_path):
    parser = CountingParser()
    service = ApiTestService(
        parser=parser,
        validator=ApiSchemaValidator(),
        generator=SchemaAcceptanceTestGenerator(),
        freezer=TestSuiteFreezer(),
        cache=LlmTestSuiteCache(tmp_path / "cache"),
    )

    first = await service.prepare(
        markdown="# Health\nGET /health",
        input_dir=tmp_path / "first",
        profile="python-basic",
        model="test-model",
    )
    second = await service.prepare(
        markdown="# Health\nGET /health",
        input_dir=tmp_path / "second",
        profile="python-basic",
        model="test-model",
    )

    assert parser.calls == 1
    assert first[5]["source"] == "llm_generated"
    assert second[5]["source"] == "llm_cache"
    assert second[5]["cache_hit"] is True
    assert first[4] == second[4]
