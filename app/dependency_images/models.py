from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DependencySpec:
    kind: str
    path: Path
    relative_path: str


@dataclass(frozen=True)
class DependencyImageResult:
    image: str
    dependency_file: str | None
    fingerprint: str | None
    cache_hit: bool

