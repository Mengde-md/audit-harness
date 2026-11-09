import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from app.schemas.acceptance_test import AcceptanceTestSuite
from app.schemas.api_schema import ApiSchemaDocument
from app.utils.process import sha256_json
from app.utils.time import utc_now

CACHE_SCHEMA_VERSION = "1"
GENERATOR_VERSION = "schema-acceptance-v1"
PARSER_PROMPT_VERSION = "api-parser-v1"


@dataclass
class CachedTestSuite:
    key: str
    schema: ApiSchemaDocument
    suite: AcceptanceTestSuite


class LlmTestSuiteCache:
    def __init__(self, root: Path):
        self.root = root

    def key_for(
        self,
        *,
        markdown: str,
        profile: str,
        model: str,
    ) -> str:
        normalized_markdown = markdown.replace("\r\n", "\n").strip()
        return sha256_json(
            {
                "markdown": normalized_markdown,
                "profile": profile,
                "model": model,
                "schema_version": CACHE_SCHEMA_VERSION,
                "generator_version": GENERATOR_VERSION,
                "prompt_version": PARSER_PROMPT_VERSION,
            }
        )

    def load(self, key: str) -> CachedTestSuite | None:
        cache_dir = self._directory(key)
        try:
            metadata = json.loads(
                (cache_dir / "metadata.json").read_text(encoding="utf-8")
            )
            if metadata.get("cache_key") != key:
                return None
            schema = ApiSchemaDocument.model_validate_json(
                (cache_dir / "api_schema.json").read_text(encoding="utf-8")
            )
            suite = AcceptanceTestSuite.model_validate_json(
                (cache_dir / "acceptance_suite.json").read_text(encoding="utf-8")
            )
            return CachedTestSuite(key=key, schema=schema, suite=suite)
        except (OSError, json.JSONDecodeError, ValidationError):
            return None

    def save(
        self,
        *,
        key: str,
        schema: ApiSchemaDocument,
        suite: AcceptanceTestSuite,
        profile: str,
        model: str,
    ) -> None:
        cache_dir = self._directory(key)
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / "api_schema.json").write_text(
            json.dumps(
                schema.model_dump(mode="json"),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        (cache_dir / "acceptance_suite.json").write_text(
            json.dumps(
                suite.model_dump(mode="json"),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        (cache_dir / "metadata.json").write_text(
            json.dumps(
                {
                    "cache_key": key,
                    "created_at": utc_now().isoformat(),
                    "schema_version": CACHE_SCHEMA_VERSION,
                    "generator_version": GENERATOR_VERSION,
                    "prompt_version": PARSER_PROMPT_VERSION,
                    "profile": profile,
                    "model": model,
                    "status": "definition_validated",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def _directory(self, key: str) -> Path:
        return self.root / key.removeprefix("sha256:")
