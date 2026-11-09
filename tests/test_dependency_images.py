import pytest

from app.core.config import Settings
from app.dependency_images.builder import DependencyImageManager
from app.dependency_images.detector import DependencyDetector
from app.dependency_images.fingerprint import DependencyFingerprint


@pytest.mark.asyncio
async def test_production_uses_preinstalled_dependencies_without_reading_manifest(
    tmp_path, monkeypatch,
):
    manager = DependencyImageManager(Settings(app_env="production", dependency_images_enabled=True))
    (tmp_path / "requirements.txt").write_text("example @ https://untrusted.invalid/package.whl\n")

    def unexpected_build(*args, **kwargs):
        pytest.fail("production dependency resolution must not inspect or build a submitted manifest")

    monkeypatch.setattr(manager.detector, "detect", unexpected_build)
    monkeypatch.setattr("app.dependency_images.builder.docker.from_env", unexpected_build)
    image = "audit-harness/python-basic:3.11"
    result = await manager.resolve(
        workspace=tmp_path, profile="python-basic", base_image=image, manifest="requirements.txt"
    )

    assert result.image == image
    assert result.dependency_file is None


def test_detects_root_requirements_before_backend(tmp_path):
    (tmp_path / "backend").mkdir()
    (tmp_path / "requirements.txt").write_text("fastapi==1.0\n")
    (tmp_path / "backend" / "requirements.txt").write_text("httpx==1.0\n")

    spec = DependencyDetector().detect(tmp_path, "python-basic")

    assert spec is not None
    assert spec.relative_path == "requirements.txt"


def test_detects_backend_requirements_for_custom_python_profile(tmp_path):
    (tmp_path / "backend").mkdir()
    requirements = tmp_path / "backend" / "requirements.txt"
    requirements.write_text("fastapi>=0.100\n")

    spec = DependencyDetector().detect(tmp_path, "custom")

    assert spec is not None
    assert spec.path == requirements


def test_detects_pyproject_when_requirements_are_absent(tmp_path):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        "[project]\n"
        "dependencies = [\n"
        '  "fastapi>=0.115.0",\n'
        '  "psycopg[binary]>=3.2.0",\n'
        "]\n",
        encoding="utf-8",
    )

    spec = DependencyDetector().detect(tmp_path, "python-basic")

    assert spec is not None
    assert spec.kind == "python-pyproject"
    assert spec.relative_path == "pyproject.toml"


def test_detects_explicit_manifest(tmp_path):
    (tmp_path / "backend").mkdir()
    manifest = tmp_path / "backend" / "pyproject.toml"
    manifest.write_text("[project]\ndependencies = []\n", encoding="utf-8")

    spec = DependencyDetector().detect(
        tmp_path,
        "python-basic",
        "backend/pyproject.toml",
    )

    assert spec is not None
    assert spec.kind == "python-pyproject"
    assert spec.path == manifest


def test_pyproject_dependencies_are_written_as_requirements(tmp_path):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        "[project]\n"
        "dependencies = [\n"
        '  "fastapi>=0.115.0",\n'
        '  "pgvector>=0.3.0",\n'
        "]\n",
        encoding="utf-8",
    )
    spec = DependencyDetector().detect(tmp_path, "python-basic")
    target = tmp_path / "requirements.txt"

    DependencyImageManager._write_requirements(spec, target)

    assert target.read_text(encoding="utf-8") == (
        "fastapi>=0.115.0\n"
        "pgvector>=0.3.0\n"
    )


def test_dependency_fingerprint_is_stable_and_content_sensitive(tmp_path):
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("fastapi==1.0\n")
    spec = DependencyDetector().detect(tmp_path, "python-basic")
    calculator = DependencyFingerprint()

    first = calculator.calculate(
        spec=spec,
        base_image="audit-harness/python-basic:3.11",
        platform_arch="arm64",
    )
    second = calculator.calculate(
        spec=spec,
        base_image="audit-harness/python-basic:3.11",
        platform_arch="arm64",
    )
    requirements.write_text("fastapi==2.0\n")
    changed = calculator.calculate(
        spec=spec,
        base_image="audit-harness/python-basic:3.11",
        platform_arch="arm64",
    )

    assert first == second
    assert first != changed
