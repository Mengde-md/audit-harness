import re
from typing import Any


_INDEXED_PART_RE = re.compile(r"^(?P<key>[^\[\]]+)\[(?P<index>\d+)\]$")


def get_json_path(payload: Any, path: str) -> tuple[bool, Any]:
    """Resolve a dot path and return one value.

    Supports simple object paths (``data.id``), numeric list indexes
    (``items.0.id`` / ``items[0].id``), and list wildcards
    (``items[].id``). Wildcards return the matched values as a list.
    """
    found, values = get_json_path_values(payload, path)
    if not found:
        return False, None
    if _has_array_wildcard(path):
        return True, values
    return True, values[0]


def get_json_path_values(payload: Any, path: str) -> tuple[bool, list[Any]]:
    """Resolve a JSON path into all matching values.

    ``items[].id`` is intentionally strict: the array must be non-empty and
    every item must contain the remaining path. This keeps generated API
    structure assertions from passing on partially malformed list responses.
    """
    nodes = [payload]
    normalized = path.removeprefix("$.")
    if normalized == "":
        return True, nodes

    for part in normalized.split("."):
        next_nodes: list[Any] = []
        for node in nodes:
            found, values = _resolve_part(node, part)
            if not found:
                return False, []
            next_nodes.extend(values)
        nodes = next_nodes
        if not nodes:
            return False, []
    return True, nodes


def _resolve_part(node: Any, part: str) -> tuple[bool, list[Any]]:
    if part.endswith("[]"):
        key = part[:-2]
        if key:
            if not isinstance(node, dict) or key not in node:
                return False, []
            node = node[key]
        if not isinstance(node, list) or not node:
            return False, []
        return True, node

    indexed_match = _INDEXED_PART_RE.match(part)
    if indexed_match:
        key = indexed_match.group("key")
        index = int(indexed_match.group("index"))
        if not isinstance(node, dict) or key not in node:
            return False, []
        value = node[key]
        if not isinstance(value, list) or index >= len(value):
            return False, []
        return True, [value[index]]

    if isinstance(node, dict) and part in node:
        return True, [node[part]]
    if isinstance(node, list) and part.isdigit() and int(part) < len(node):
        return True, [node[int(part)]]
    return False, []


def _has_array_wildcard(path: str) -> bool:
    return any(part.endswith("[]") for part in path.removeprefix("$.").split("."))


def value_matches_type(value: Any, expected: str) -> bool:
    checks = {
        "string": lambda item: isinstance(item, str),
        "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
        "number": lambda item: isinstance(item, (int, float))
        and not isinstance(item, bool),
        "boolean": lambda item: isinstance(item, bool),
        "object": lambda item: isinstance(item, dict),
        "array": lambda item: isinstance(item, list),
        "null": lambda item: item is None,
    }
    checker = checks.get(expected)
    return checker(value) if checker else False
