from app.core.config import Settings
from app.core.exceptions import InvalidInputError


class ImagePolicy:
    def __init__(self, settings: Settings):
        self.settings = settings

    def for_profile(self, profile: str, python_version: str | None = None) -> str:
        if profile not in {"python-basic", "custom"}:
            raise InvalidInputError(f"no allowed image configured for profile: {profile}")
        version = python_version or "3.11"
        image = self.settings.python_runtime_images.get(version)
        if image is None and version == "3.11":
            image = self.settings.python_sandbox_image
        if image is None or image not in self.settings.sandbox_allowed_images:
            allowed = ", ".join(sorted(self.settings.python_runtime_images))
            raise InvalidInputError(
                f"unsupported python runtime version: {version}; "
                f"allowed versions: {allowed}"
            )
        return image
