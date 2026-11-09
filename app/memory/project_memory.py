import json
import re
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from app.memory.models import ProjectMemory
from app.schemas.acceptance_test import AcceptanceTestSuite
from app.schemas.mock_llm import LlmMockRule

ISSUE_HEADING = re.compile(r"^##\s+KI-[A-Za-z0-9_-]+", re.MULTILINE)


class ProjectMemoryLoader:
    def load(self, workspace: Path) -> ProjectMemory:
        root = workspace / ".audit-harness"
        memory = ProjectMemory(root=root if root.is_dir() else None)
        if not root.is_dir():
            return memory

        memory.project_markdown = self._read_optional(root / "PROJECT.md")
        memory.known_issues_markdown = self._read_optional(
            root / "KNOWN_ISSUES.md"
        )
        if memory.known_issues_markdown:
            memory.known_issue_count = len(
                ISSUE_HEADING.findall(memory.known_issues_markdown)
            )
        memory.mock_rules = self._load_mock_rules(
            root / "MOCK_RULES.json",
            memory.warnings,
        )
        memory.acceptance_suite = self._load_acceptance_suite(
            root / "tests" / "api-acceptance.json",
            memory.warnings,
        )
        return memory

    @staticmethod
    def _read_optional(path: Path) -> str | None:
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8", errors="replace")

    @staticmethod
    def _load_mock_rules(path: Path, warnings: list[str]) -> list[LlmMockRule]:
        if not path.is_file():
            return []
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            raw_rules = value.get("rules", []) if isinstance(value, dict) else value
            return TypeAdapter(list[LlmMockRule]).validate_python(raw_rules)
        except (json.JSONDecodeError, ValidationError, TypeError) as exc:
            warnings.append(f"invalid MOCK_RULES.json: {exc}")
            return []

    @staticmethod
    def _load_acceptance_suite(
        path: Path,
        warnings: list[str],
    ) -> AcceptanceTestSuite | None:
        if not path.is_file():
            return None
        try:
            return AcceptanceTestSuite.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except ValidationError as exc:
            warnings.append(f"invalid tests/api-acceptance.json: {exc}")
            return None
