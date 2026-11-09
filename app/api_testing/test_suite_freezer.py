import json
from pathlib import Path

from app.schemas.acceptance_test import AcceptanceTestSuite
from app.schemas.api_schema import ApiSchemaDocument
from app.utils.process import sha256_json


class TestSuiteFreezer:
    def freeze(
        self,
        *,
        input_dir: Path,
        schema: ApiSchemaDocument,
        suite: AcceptanceTestSuite,
    ) -> tuple[Path, Path, str]:
        input_dir.mkdir(parents=True, exist_ok=True)
        schema_path = input_dir / "api_schema.json"
        suite_path = input_dir / "acceptance_suite.json"
        schema_path.write_text(
            json.dumps(schema.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        suite_data = suite.model_dump(mode="json")
        suite_path.write_text(
            json.dumps(suite_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return schema_path, suite_path, sha256_json(suite_data)

