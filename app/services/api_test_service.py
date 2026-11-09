"""API test preparation service.

This service turns Markdown API documentation or project-provided memory into a
frozen executable acceptance suite.
"""

from pathlib import Path

from app.api_testing.schema_validator import ApiSchemaValidator
from app.api_testing.test_suite_freezer import TestSuiteFreezer
from app.core.error_codes import ErrorCode
from app.core.exceptions import ApiDocumentError
from app.generators.acceptance_test_generator import AcceptanceTestGenerator
from app.memory.test_suite_cache import LlmTestSuiteCache
from app.parsers.api_document_parser import ApiDocumentParser
from app.schemas.acceptance_test import AcceptanceTestSuite
from app.schemas.api_schema import ApiSchemaDocument


class ApiTestService:
    """Parse, validate, generate/cache, and freeze API acceptance tests."""

    def __init__(
        self,
        parser: ApiDocumentParser,
        validator: ApiSchemaValidator,
        generator: AcceptanceTestGenerator,
        freezer: TestSuiteFreezer,
        cache: LlmTestSuiteCache | None = None,
    ):
        self.parser = parser
        self.validator = validator
        self.generator = generator
        self.freezer = freezer
        self.cache = cache

    async def prepare(
        self,
        *,
        markdown: str,
        input_dir: Path,
        profile: str = "custom",
        model: str = "",
        project_suite: AcceptanceTestSuite | None = None,
    ) -> tuple[
        ApiSchemaDocument,
        AcceptanceTestSuite,
        Path,
        Path,
        str,
        dict,
    ]:
        """Prepare schema and acceptance suite artifacts for one audit."""
        cache_key = (
            self.cache.key_for(markdown=markdown, profile=profile, model=model)
            if self.cache
            else None
        )
        if project_suite is not None:
            # Project memory is treated as an explicit suite override; in that
            # mode the Markdown schema is no longer the source of executable tests.
            schema = ApiSchemaDocument(
                endpoints=[],
                warnings=["API acceptance suite loaded from project memory."],
            )
            suite = project_suite
            source = "project_memory"
        else:
            cached = self.cache.load(cache_key) if self.cache and cache_key else None
            if cached:
                # LLM-generated suites are cached by document/profile/model so
                # repeated audits stay deterministic and cheaper.
                schema = cached.schema
                suite = cached.suite
                source = "llm_cache"
            else:
                schema = self.validator.validate(await self.parser.parse(markdown))
                suite = await self.generator.generate(schema)
                source = "llm_generated"
                if self.cache and cache_key:
                    self.cache.save(
                        key=cache_key,
                        schema=schema,
                        suite=suite,
                        profile=profile,
                        model=model,
                    )
        if not suite.tests:
            # A suite with no executable tests would make the audit look green
            # without validating any API behavior.
            raise ApiDocumentError(
                "api_document_insufficient: no executable tests",
                error=ErrorCode.API_DOCUMENT_INSUFFICIENT,
            )
        schema_path, suite_path, suite_hash = self.freezer.freeze(
            input_dir=input_dir,
            schema=schema,
            suite=suite,
        )
        return (
            schema,
            suite,
            schema_path,
            suite_path,
            suite_hash,
            {
                "source": source,
                "cache_key": cache_key,
                "cache_hit": source == "llm_cache",
            },
        )
