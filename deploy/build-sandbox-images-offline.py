"""Build the three audit images from preloaded Python bases and local wheels."""

from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
from tempfile import TemporaryDirectory


RELEASE = Path(__file__).resolve().parents[1]
VERSIONS = ("3.10", "3.11", "3.12")


def build_images(wheels: Path, release: Path) -> None:
    template = (release / "docker/sandbox/python-basic.Dockerfile").read_text()
    command = "RUN pip install --no-cache-dir"
    if template.count(command) != 1:
        raise RuntimeError("The base Dockerfile's dependency step has changed")
    dockerfile = template.replace(
        command,
        "COPY wheels /tmp/audit-wheels\n"
        "RUN pip install --no-index --find-links=/tmp/audit-wheels --no-cache-dir",
    )
    with tarfile.open(wheels, "r:gz") as archive:
        members = archive.getmembers()
        for member in members:
            name = Path(member.name)
            if (
                not member.isfile()
                or len(name.parts) != 2
                or name.parts[0] not in {"py310", "py311", "py312"}
                or name.suffix != ".whl"
            ):
                raise RuntimeError(f"Unexpected file in wheel archive: {member.name}")

        for version in VERSIONS:
            wheel_group = "py" + version.replace(".", "")
            group = [member for member in members if Path(member.name).parts[0] == wheel_group]
            if not group:
                raise RuntimeError(f"No wheels for Python {version}")
            with TemporaryDirectory(prefix=f"audit-image-{version}-") as directory:
                context = Path(directory)
                wheel_dir = context / "wheels"
                wheel_dir.mkdir()
                for member in group:
                    path = wheel_dir / Path(member.name).name
                    with archive.extractfile(member) as source, path.open("xb") as destination:
                        shutil.copyfileobj(source, destination)
                shutil.copytree(release / "app", context / "app", ignore=shutil.ignore_patterns("__pycache__"))
                (context / "Dockerfile").write_text(dockerfile)
                subprocess.run([
                    "docker", "build", "--network", "none",
                    "--build-arg", f"BASE_IMAGE=docker.io/library/python:{version}-slim",
                    "-t", f"audit-harness/python-basic:{version}", str(context),
                ], check=True)


if __name__ == "__main__":
    release = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else RELEASE
    build_images(Path(sys.argv[1]), release)
