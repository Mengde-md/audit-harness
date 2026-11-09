from dataclasses import dataclass, field
from pathlib import Path

from app.schemas.acceptance_test import AcceptanceTestSuite
from app.schemas.mock_llm import LlmMockRule


@dataclass
class ProjectMemory:
    root: Path | None = None
    project_markdown: str | None = None
    known_issues_markdown: str | None = None
    known_issue_count: int = 0
    mock_rules: list[LlmMockRule] = field(default_factory=list)
    acceptance_suite: AcceptanceTestSuite | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def loaded_files(self) -> list[str]:
        files: list[str] = []
        if self.project_markdown is not None:
            files.append("PROJECT.md")
        if self.known_issues_markdown is not None:
            files.append("KNOWN_ISSUES.md")
        if self.mock_rules:
            files.append("MOCK_RULES.json")
        if self.acceptance_suite is not None:
            files.append("tests/api-acceptance.json")
        return files
