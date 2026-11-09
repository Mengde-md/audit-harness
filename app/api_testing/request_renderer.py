"""Render request templates for acceptance tests."""

import re
from copy import deepcopy
from typing import Any

from app.schemas.acceptance_test import AcceptanceTest

VARIABLE_PATTERN = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_.-]*)\s*\}\}")


class RequestRenderer:
    """Apply explicit ``use`` injections and ``{{variable}}`` substitutions."""

    def render(
        self,
        test: AcceptanceTest,
        variables: dict[str, Any],
    ) -> tuple[dict[str, Any], list[str]]:
        """Render one test request into path/query/headers/body parts."""
        failures: list[str] = []
        request_parts = {
            "path": test.path,
            "query": deepcopy(test.query),
            "headers": deepcopy(test.headers),
            "body": deepcopy(test.body),
        }
        self._apply_use(request_parts, test.use, variables, failures)
        # Apply generic template rendering after explicit injections so injected
        # values can themselves contain templates.
        rendered = {
            key: self._render_value(value, variables, failures)
            for key, value in request_parts.items()
        }
        return rendered, failures

    def _apply_use(
        self,
        request_parts: dict[str, Any],
        use: dict[str, str],
        variables: dict[str, Any],
        failures: list[str],
    ) -> None:
        """Apply explicit target-to-template mappings before full rendering."""
        for target, template in use.items():
            value = self._render_value(template, variables, failures)
            if failures:
                continue
            self._set_target(request_parts, target, value, failures)

    def _set_target(
        self,
        request_parts: dict[str, Any],
        target: str,
        value: Any,
        failures: list[str],
    ) -> None:
        """Set a rendered value into path/query/headers/body by dotted target."""
        if target == "path":
            request_parts["path"] = value
            return
        parts = target.split(".")
        if len(parts) < 2 or parts[0] not in {"query", "headers", "body"}:
            failures.append(f"unsupported use target: {target}")
            return
        root_name = parts[0]
        if request_parts[root_name] is None:
            request_parts[root_name] = {}
        current = request_parts[root_name]
        if not isinstance(current, dict):
            failures.append(f"use target is not an object: {root_name}")
            return
        for part in parts[1:-1]:
            # Create missing intermediate dictionaries so "body.data.id" works
            # even when the original request body only had "body": {}.
            child = current.get(part)
            if child is None:
                child = {}
                current[part] = child
            if not isinstance(child, dict):
                failures.append(f"use target parent is not an object: {target}")
                return
            current = child
        current[parts[-1]] = value

    def _render_value(
        self,
        value: Any,
        variables: dict[str, Any],
        failures: list[str],
    ) -> Any:
        """Recursively render strings, dictionaries, and arrays."""
        if isinstance(value, str):
            full_match = VARIABLE_PATTERN.fullmatch(value.strip())
            if full_match:
                # A full-variable value preserves the original type, allowing
                # numeric ids or booleans to remain non-string in JSON bodies.
                return self._resolve_variable(full_match.group(1), value, variables, failures)

            def replace(match: re.Match[str]) -> str:
                """Replace partial string templates as strings."""
                name = match.group(1)
                if name not in variables:
                    failures.append(f"missing variable: {name}")
                    return match.group(0)
                return str(variables[name])

            return VARIABLE_PATTERN.sub(replace, value)
        if isinstance(value, dict):
            return {
                key: self._render_value(item, variables, failures)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self._render_value(item, variables, failures) for item in value]
        return value

    @staticmethod
    def _resolve_variable(
        name: str,
        original: str,
        variables: dict[str, Any],
        failures: list[str],
    ) -> Any:
        """Resolve one variable name or record a render failure."""
        if name not in variables:
            failures.append(f"missing variable: {name}")
            return original
        return variables[name]
