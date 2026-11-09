"""Build and cache project-specific dependency Docker images."""

import asyncio
import logging
import shutil
import tempfile
import tomllib
from pathlib import Path

from docker.errors import BuildError, DockerException, ImageNotFound

import docker
from app.core.config import Settings
from app.core.error_codes import ErrorCode
from app.core.exceptions import SandboxError
from app.dependency_images.detector import DependencyDetector
from app.dependency_images.fingerprint import DependencyFingerprint
from app.dependency_images.models import DependencyImageResult, DependencySpec


class DependencyImageManager:
    """Resolve a sandbox image that already contains project dependencies."""

    def __init__(
        self,
        settings: Settings,
        detector: DependencyDetector | None = None,
        fingerprint: DependencyFingerprint | None = None,
    ):
        self.settings = settings
        self.detector = detector or DependencyDetector()
        self.fingerprint = fingerprint or DependencyFingerprint()
        self.logger = logging.getLogger("audit_harness.dependencies")

    async def resolve(
        self,
        *,
        workspace: Path,
        profile: str,
        base_image: str,
        manifest: str | None = None,
    ) -> DependencyImageResult:
        """Return either the base image or a cached dependency image."""
        # Public audits use preinstalled wheels from the approved base image.
        # Building from a submitted manifest would expose a networked build to
        # untrusted dependency metadata before the offline sandbox starts.
        if (
            self.settings.app_env.lower() in {"production", "prod"}
            or not self.settings.dependency_images_enabled
        ):
            return DependencyImageResult(base_image, None, None, True)

        spec = self.detector.detect(workspace, profile, manifest)
        if spec is None:
            if manifest:
                raise SandboxError(
                    f"dependency manifest not found: {manifest}",
                    error=ErrorCode.DEPENDENCY_DETECTION_ERROR,
                    context={"dependency_file": manifest},
                )
            self.logger.info(
                "DEPENDENCY_NOT_FOUND | profile=%s | image=%s | manifest=%s",
                profile,
                base_image,
                manifest or "auto",
            )
            return DependencyImageResult(base_image, None, None, True)

        try:
            client = await asyncio.to_thread(
                docker.from_env,
                timeout=self.settings.dependency_build_timeout_seconds + 60,
            )
            info = await asyncio.to_thread(client.info)
            architecture = str(info.get("Architecture", "unknown"))
            base_image_object = await asyncio.to_thread(client.images.get, base_image)
            # The base image id and architecture are part of the fingerprint so
            # dependency layers are rebuilt when the sandbox runtime changes.
            fingerprint = self.fingerprint.calculate(
                spec=spec,
                base_image=f"{base_image}@{base_image_object.id}",
                platform_arch=architecture,
            )
            image = f"audit-harness/python-deps:{fingerprint[:16]}"
            try:
                await asyncio.to_thread(client.images.get, image)
            except ImageNotFound:
                # Build only on cache miss; acceptance containers later run with
                # restricted network, so dependency download is isolated here.
                await self._build(client, spec, base_image, image, fingerprint)
                cache_hit = False
            else:
                cache_hit = True
                self.logger.info(
                    "DEPENDENCY_IMAGE_CACHE_HIT | image=%s | file=%s",
                    image,
                    spec.relative_path,
                )
            return DependencyImageResult(
                image=image,
                dependency_file=spec.relative_path,
                fingerprint=fingerprint,
                cache_hit=cache_hit,
            )
        except SandboxError:
            raise
        except (DockerException, OSError) as exc:
            self.logger.exception(
                "DEPENDENCY_IMAGE_ERROR | file=%s | base_image=%s",
                spec.relative_path,
                base_image,
            )
            raise SandboxError(
                f"dependency image preparation failed: {exc}",
                error=ErrorCode.DEPENDENCY_IMAGE_BUILD_ERROR,
                context={
                    "dependency_file": spec.relative_path,
                    "base_image": base_image,
                },
            ) from exc

    async def _build(
        self,
        client,
        spec: DependencySpec,
        base_image: str,
        image: str,
        fingerprint: str,
    ) -> None:
        """Build one dependency image from a temporary Docker context."""
        context = Path(tempfile.mkdtemp(prefix="audit-deps-"))
        try:
            self._write_requirements(spec, context / "requirements.txt")
            (context / "Dockerfile").write_text(
                self._dockerfile(),
                encoding="utf-8",
            )
            self.logger.info(
                "DEPENDENCY_IMAGE_BUILD_START | image=%s | file=%s "
                "| fingerprint=%s",
                image,
                spec.relative_path,
                fingerprint,
            )
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(
                        client.images.build,
                        path=str(context),
                        tag=image,
                        buildargs={
                            "BASE_IMAGE": base_image,
                            "PIP_INDEX_URL": self.settings.dependency_pip_index_url,
                        },
                        rm=True,
                        forcerm=True,
                        pull=False,
                        labels={
                            # Labels make future cleanup/inspection of managed
                            # dependency images straightforward.
                            "audit-harness.managed": "true",
                            "audit-harness.image-type": "python-dependencies",
                            "audit-harness.fingerprint": fingerprint,
                        },
                    ),
                    timeout=self.settings.dependency_build_timeout_seconds,
                )
            except TimeoutError as exc:
                raise SandboxError(
                    "dependency image build timed out",
                    error=ErrorCode.DEPENDENCY_IMAGE_BUILD_ERROR,
                    context={"image": image, "dependency_file": spec.relative_path},
                ) from exc
            except BuildError as exc:
                build_log = "".join(
                    item.get("stream", "") for item in (exc.build_log or [])
                )
                raise SandboxError(
                    f"dependency image build failed: {build_log[-8000:]}",
                    error=ErrorCode.DEPENDENCY_IMAGE_BUILD_ERROR,
                    context={"image": image, "dependency_file": spec.relative_path},
                ) from exc
            self.logger.info(
                "DEPENDENCY_IMAGE_BUILD_DONE | image=%s | file=%s",
                image,
                spec.relative_path,
            )
        finally:
            shutil.rmtree(context, ignore_errors=True)

    @staticmethod
    def _dockerfile() -> str:
        """Return the minimal Dockerfile used for dependency image builds."""
        return """\
ARG BASE_IMAGE
FROM ${BASE_IMAGE}

ARG PIP_INDEX_URL
USER root
COPY requirements.txt /tmp/audit-requirements.txt
RUN pip install --no-cache-dir \
    --index-url "${PIP_INDEX_URL}" \
    -r /tmp/audit-requirements.txt
USER 1000:1000
WORKDIR /workspace
"""

    @staticmethod
    def _write_requirements(spec: DependencySpec, target: Path) -> None:
        """Normalize supported dependency manifests to requirements.txt."""
        if spec.kind == "python-requirements":
            shutil.copyfile(spec.path, target)
            return
        if spec.kind == "python-pyproject":
            data = tomllib.loads(spec.path.read_text(encoding="utf-8"))
            dependencies = data.get("project", {}).get("dependencies", [])
            target.write_text(
                "\n".join(str(item) for item in dependencies) + "\n",
                encoding="utf-8",
            )
            return
        raise SandboxError(
            f"unsupported dependency spec kind: {spec.kind}",
            error=ErrorCode.DEPENDENCY_DETECTION_ERROR,
            context={"dependency_file": spec.relative_path},
        )
