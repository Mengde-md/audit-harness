from pathlib import Path

from app.dependency_images.models import DependencySpec
from app.core.exceptions import InvalidInputError


class DependencyDetector:
    PYTHON_REQUIREMENTS = (
        "requirements.txt",
        "backend/requirements.txt",
    )
    PYTHON_PYPROJECT = (
        "pyproject.toml",
        "backend/pyproject.toml",
    )

    def detect(
        self,
        workspace: Path,
        profile: str,
        manifest: str | None = None,
    ) -> DependencySpec | None:
        if profile not in {"python-basic", "custom"}:
            return None
        if manifest:
            path = (workspace / manifest).resolve()
            if not path.is_relative_to(workspace.resolve()):
                raise InvalidInputError("dependency manifest must be inside the repository")
            if not path.is_file():
                return None
            if path.stat().st_size > 2 * 1024 * 1024:
                raise InvalidInputError("dependency manifest exceeds the 2 MiB limit")
            if path.name == "pyproject.toml":
                kind = "python-pyproject"
            else:
                kind = "python-requirements"
            return DependencySpec(kind=kind, path=path, relative_path=manifest)
        for relative_path in self.PYTHON_REQUIREMENTS:
            path = workspace / relative_path
            if path.is_file():
                return DependencySpec(
                    kind="python-requirements",
                    path=path,
                    relative_path=relative_path,
                )
        for relative_path in self.PYTHON_PYPROJECT:
            path = workspace / relative_path
            if path.is_file():
                return DependencySpec(
                    kind="python-pyproject",
                    path=path,
                    relative_path=relative_path,
                )
        return None
