"""HTTP API acceptance runner.

The runner executes generated API tests against the audited service. Tests can
form a dependency graph via ``depends_on``; the runner schedules ready nodes in
batches and propagates upstream failures to downstream tests.
"""

import argparse
import asyncio
import json
import time
from pathlib import Path
from typing import Any

import httpx

from app.api_testing.json_assertions import JsonAssertionEngine, JsonExtractor
from app.api_testing.request_renderer import RequestRenderer
from app.api_testing.sse_assertions import SseAssertionEngine
from app.schemas.acceptance_test import (
    AcceptanceRunResult,
    AcceptanceTest,
    AcceptanceTestResult,
    AcceptanceTestSuite,
)


class HttpAcceptanceRunner:
    """Execute API acceptance tests with JSON/SSE assertions and data chaining."""

    def __init__(
        self,
        *,
        renderer: RequestRenderer | None = None,
        json_assertions: JsonAssertionEngine | None = None,
        json_extractor: JsonExtractor | None = None,
        sse_assertions: SseAssertionEngine | None = None,
    ):
        self.renderer = renderer or RequestRenderer()
        self.json_assertions = json_assertions or JsonAssertionEngine()
        self.json_extractor = json_extractor or JsonExtractor()
        self.sse_assertions = sse_assertions or SseAssertionEngine()

    async def wait_until_ready(
        self,
        base_url: str,
        timeout_seconds: int,
    ) -> None:
        """Poll the target service until it accepts HTTP requests or times out."""
        deadline = time.monotonic() + timeout_seconds
        async with httpx.AsyncClient() as client:
            while time.monotonic() < deadline:
                try:
                    await client.get(base_url, timeout=1)
                    return
                except httpx.HTTPError:
                    await asyncio.sleep(0.25)
        raise TimeoutError(f"service did not become ready at {base_url}")

    async def run(
        self,
        suite: AcceptanceTestSuite,
        base_url: str,
    ) -> AcceptanceRunResult:
        """Run the test suite as a DAG instead of a purely linear list.

        Each loop iteration runs all tests whose dependencies have passed. This
        keeps independent endpoints concurrent while still preserving data-chain
        safety for flows like create -> upload -> query.
        """
        results_by_id: dict[str, AcceptanceTestResult] = {}
        variables: dict[str, Any] = {}
        completed: set[str] = set()
        blocked: set[str] = set()
        tests_by_id = {test.id: test for test in suite.tests}
        pending = {test.id for test in suite.tests}
        async with httpx.AsyncClient(base_url=base_url, timeout=30) as client:
            while pending:
                # Unknown dependencies are configuration/test-generation errors,
                # so fail them before trying to compute the next ready batch.
                self._fail_unknown_dependencies(
                    pending,
                    tests_by_id,
                    results_by_id,
                    blocked,
                )
                ready = [
                    test
                    for test_id, test in tests_by_id.items()
                    if test_id in pending
                    and all(dependency in completed for dependency in test.depends_on)
                ]
                if not ready:
                    # No runnable node means every remaining test is either
                    # blocked by a failed upstream node or participates in a cycle.
                    self._fail_blocked_or_cyclic_dependencies(
                        pending,
                        tests_by_id,
                        results_by_id,
                        blocked,
                    )
                    break
                rendered_variables = dict(variables)
                # Use a variable snapshot for the whole batch; tests in the same
                # DAG layer must not consume each other's outputs implicitly.
                batch_results = await asyncio.gather(
                    *[
                        self._run_one(client, test, rendered_variables)
                        for test in ready
                    ]
                )
                for test, result in zip(ready, batch_results, strict=True):
                    pending.remove(test.id)
                    results_by_id[test.id] = result
                    if result.passed:
                        completed.add(test.id)
                        # Store both flat and scoped names. Flat keeps old suites
                        # compatible; scoped avoids collisions in explicit chains.
                        self._store_extracted_variables(
                            test.id,
                            result.extracted,
                            variables,
                        )
                    else:
                        blocked.add(test.id)
        results = [results_by_id[test.id] for test in suite.tests]
        passed = sum(item.passed for item in results)
        return AcceptanceRunResult(
            total=len(results),
            passed=passed,
            failed=len(results) - passed,
            tests=results,
        )

    async def _run_one(
        self,
        client: httpx.AsyncClient,
        test: AcceptanceTest,
        variables: dict[str, Any] | None = None,
    ) -> AcceptanceTestResult:
        """Render, send, assert, and extract variables for a single API test."""
        started = time.monotonic()
        failures: list[str] = []
        status_code: int | None = None
        excerpt = ""
        sse_events: list[dict[str, Any]] = []
        extracted: dict[str, Any] = {}
        variables = variables or {}
        try:
            rendered, render_failures = self.renderer.render(test, variables)
            failures.extend(render_failures)
            if failures:
                # Render failures usually mean a broken data dependency, so skip
                # the HTTP request and report the exact missing variable.
                duration_ms = int((time.monotonic() - started) * 1000)
                return AcceptanceTestResult(
                    test_id=test.id,
                    name=test.name,
                    passed=False,
                    status_code=None,
                    duration_ms=duration_ms,
                    failures=failures,
                )
            response = await client.request(
                test.method,
                rendered["path"],
                params=rendered["query"],
                headers=rendered["headers"],
                json=rendered["body"],
            )
            status_code = response.status_code
            excerpt = response.text[:2000]
            if response.status_code != test.expected_status:
                failures.append(
                    f"expected status {test.expected_status}, got {response.status_code}"
                )
            if test.response_mode == "sse":
                # Streaming endpoints are asserted by SSE event structure rather
                # than by one final JSON document.
                sse_events, sse_failures = self._assert_sse(response.text)
                failures.extend(sse_failures)
            elif test.assertions or test.extract:
                try:
                    payload = response.json()
                except ValueError:
                    failures.append("response is not valid JSON")
                else:
                    if test.assertions:
                        failures.extend(
                            self.json_assertions.assert_json(payload, test)
                        )
                    extracted, extract_failures = self.json_extractor.extract(
                        payload,
                        test,
                    )
                    failures.extend(extract_failures)
        except httpx.HTTPError as exc:
            failures.append(f"request failed: {exc}")
        duration_ms = int((time.monotonic() - started) * 1000)
        return AcceptanceTestResult(
            test_id=test.id,
            name=test.name,
            passed=not failures,
            status_code=status_code,
            duration_ms=duration_ms,
            failures=failures,
            sse_events=sse_events,
            extracted=extracted,
            response_excerpt=excerpt,
        )

    def _assert_sse(self, response_text: str) -> tuple[list[dict], list[str]]:
        """Parse and assert an SSE response body."""
        return self.sse_assertions.assert_sse(response_text)

    def _fail_unknown_dependencies(
        self,
        pending: set[str],
        tests_by_id: dict[str, AcceptanceTest],
        results_by_id: dict[str, AcceptanceTestResult],
        blocked: set[str],
    ) -> None:
        """Fail tests that reference dependency ids not present in the suite."""
        known = set(tests_by_id)
        for test_id in list(pending):
            test = tests_by_id[test_id]
            missing = [item for item in test.depends_on if item not in known]
            if not missing:
                continue
            results_by_id[test_id] = AcceptanceTestResult(
                test_id=test.id,
                name=test.name,
                passed=False,
                duration_ms=0,
                failures=["missing dependency: " + ", ".join(missing)],
            )
            pending.remove(test_id)
            blocked.add(test_id)

    def _fail_blocked_or_cyclic_dependencies(
        self,
        pending: set[str],
        tests_by_id: dict[str, AcceptanceTest],
        results_by_id: dict[str, AcceptanceTestResult],
        blocked: set[str],
    ) -> None:
        """Mark remaining tests as blocked by upstream failures or graph cycles."""
        blocked_by_test = {
            test_id: [
                item for item in tests_by_id[test_id].depends_on if item in blocked
            ]
            for test_id in pending
        }
        for test_id in list(pending):
            test = tests_by_id[test_id]
            blocked_dependencies = blocked_by_test[test_id]
            if blocked_dependencies:
                reason = "blocked dependency: " + ", ".join(blocked_dependencies)
            else:
                reason = "cyclic dependency detected"
            results_by_id[test.id] = AcceptanceTestResult(
                test_id=test.id,
                name=test.name,
                passed=False,
                duration_ms=0,
                failures=[reason],
            )
            pending.remove(test_id)
            blocked.add(test_id)

    @staticmethod
    def _store_extracted_variables(
        test_id: str,
        extracted: dict[str, Any],
        variables: dict[str, Any],
    ) -> None:
        """Store extracted response values for downstream request rendering."""
        for name, value in extracted.items():
            variables[name] = value
            variables[f"{test_id}.{name}"] = value


async def _main() -> int:
    """CLI entrypoint used inside the sandbox container."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--startup-timeout", type=int, default=30)
    args = parser.parse_args()

    suite = AcceptanceTestSuite.model_validate_json(
        Path(args.suite).read_text(encoding="utf-8")
    )
    runner = HttpAcceptanceRunner()
    base_url = f"http://127.0.0.1:{args.port}"
    await runner.wait_until_ready(base_url, args.startup_timeout)
    result = await runner.run(suite, base_url)
    Path(args.output).write_text(
        json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False))
    return 0 if result.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
