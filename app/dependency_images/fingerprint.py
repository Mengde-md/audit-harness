import hashlib

from app.dependency_images.models import DependencySpec


class DependencyFingerprint:
    TEMPLATE_VERSION = "python-requirements-v1"

    def calculate(
        self,
        *,
        spec: DependencySpec,
        base_image: str,
        platform_arch: str,
    ) -> str:
        digest = hashlib.sha256()
        digest.update(self.TEMPLATE_VERSION.encode())
        digest.update(b"\0")
        digest.update(base_image.encode())
        digest.update(b"\0")
        digest.update(platform_arch.encode())
        digest.update(b"\0")
        digest.update(spec.path.read_bytes())
        return digest.hexdigest()

