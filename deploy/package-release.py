"""Build a self-contained Linux Python 3.11 application bundle locally."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
version = sys.argv[1]
if not re.fullmatch(r"\d{8}-\d{6}", version):
    raise SystemExit("Invalid release")
stage = ROOT / ".release-stage" / version
stage.mkdir(parents=True, exist_ok=False)
for name in ["app", "docker", "migrations", "scripts", "deploy", "tests"]:
    shutil.copytree(ROOT / name, stage / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
for name in ["pyproject.toml", "alembic.ini", "README.md", "requirements-linux.lock", ".env.example"]:
    shutil.copyfile(ROOT / name, stage / name)
shutil.copyfile(ROOT / "deploy/start.sh", stage / "start.sh")
for path in stage.rglob("*.sh"):
    path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n"))
vendor = stage / "vendor"
vendor.mkdir()
wheel_manifest = {}
for wheel in sorted((ROOT / ".release-wheels").glob("*.whl")):
    if "win_" in wheel.name:
        raise RuntimeError("Windows wheel in Linux release")
    wheel_manifest[wheel.name] = hashlib.sha256(wheel.read_bytes()).hexdigest()
    with zipfile.ZipFile(wheel) as archive:
        for item in archive.infolist():
            target = (vendor / item.filename).resolve()
            if not target.is_relative_to(vendor.resolve()):
                raise RuntimeError("Unsafe wheel entry")
        archive.extractall(vendor)
if not wheel_manifest:
    raise RuntimeError("No Linux dependencies")
(stage / "dependency-manifest.json").write_text(json.dumps(wheel_manifest, indent=2))
manifest = {str(p.relative_to(stage)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in stage.rglob("*") if p.is_file()}
(stage / "manifest.json").write_text(json.dumps(manifest, indent=2))
archive_path = stage.parent / f"audit-harness-{version}.tar.gz"
with tarfile.open(archive_path, "w:gz") as archive:
    for path in stage.iterdir():
        archive.add(path, arcname=path.name)
print(json.dumps({"version": version, "archive": str(archive_path),
                  "sha256": hashlib.sha256(archive_path.read_bytes()).hexdigest(),
                  "bytes": archive_path.stat().st_size, "files": len(manifest)}))
