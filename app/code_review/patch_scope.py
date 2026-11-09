"""Patch parsing helpers for change-mode code review."""

from pathlib import Path


def extract_python_files_from_patch(patch: str | None) -> list[Path]:
    """Extract changed Python file paths from a unified git diff."""
    if not patch:
        return []
    paths: list[Path] = []
    seen: set[str] = set()
    for line in patch.splitlines():
        if not line.startswith("diff --git "):
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        candidate = parts[3]
        if candidate.startswith("b/"):
            candidate = candidate[2:]
        if candidate == "/dev/null" or not candidate.endswith(".py"):
            continue
        normalized = str(Path(candidate))
        if normalized not in seen:
            seen.add(normalized)
            paths.append(Path(normalized))
    return paths

