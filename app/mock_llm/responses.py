import json
from pathlib import Path
from typing import Any

from app.mock_llm.profiles import load_profile
from app.schemas.mock_llm import LlmMockConfig, LlmMockResponse, LlmMockRule


class MockResponseEngine:
    def __init__(self, config: LlmMockConfig | None = None):
        self.config = config or LlmMockConfig()
        self.project_rules = self._sort_rules(self.config.rules)
        profile_rules, profile_fallback = load_profile(self.config.profile)
        self.profile_rules = self._sort_rules(profile_rules)
        self.fallback = self.config.fallback or profile_fallback

    @classmethod
    def from_path(cls, path: Path) -> "MockResponseEngine":
        content = path.read_text(encoding="utf-8")
        return cls(LlmMockConfig.model_validate_json(content))

    def generate(self, payload: dict[str, Any]) -> tuple[str, str]:
        prompt = _flatten_prompt(payload).lower()
        stream = bool(payload.get("stream"))
        for source, rules in (
            ("project", self.project_rules),
            ("profile", self.profile_rules),
        ):
            for rule in rules:
                if _matches(rule, prompt, stream):
                    return f"{source}:{rule.id}", _render(rule.response)
        if self.fallback is None:
            raise LookupError(
                "no mock LLM rule matched and no fallback is configured"
            )
        return "fallback", _render(self.fallback)

    @staticmethod
    def _sort_rules(rules: list[LlmMockRule]) -> list[LlmMockRule]:
        return sorted(rules, key=lambda rule: rule.priority, reverse=True)


def _matches(rule: LlmMockRule, prompt: str, stream: bool) -> bool:
    matcher = rule.match
    if matcher.stream is not None and matcher.stream != stream:
        return False
    contains_all = [item.lower() for item in matcher.contains_all]
    contains_any = [item.lower() for item in matcher.contains_any]
    if contains_all and not all(item in prompt for item in contains_all):
        return False
    return not contains_any or any(item in prompt for item in contains_any)


def _render(response: LlmMockResponse) -> str:
    if response.format == "json":
        return json.dumps(response.content, ensure_ascii=False)
    return str(response.content)


def _flatten_prompt(payload: dict[str, Any]) -> str:
    parts: list[str] = []
    system = payload.get("system")
    if isinstance(system, str):
        parts.append(system)
    elif isinstance(system, list):
        parts.extend(_content_text(item) for item in system)

    for message in payload.get("messages", []):
        if isinstance(message, dict):
            parts.append(_content_text(message.get("content")))
    return "\n".join(part for part in parts if part)


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        return str(content.get("text", ""))
    if isinstance(content, list):
        return "\n".join(_content_text(item) for item in content)
    return ""
