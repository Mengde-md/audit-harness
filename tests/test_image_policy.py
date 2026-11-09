import pytest

from app.core.config import Settings
from app.core.exceptions import InvalidInputError
from app.runners.image_policy import ImagePolicy


def test_image_policy_maps_allowed_python_runtime_version(tmp_path):
    settings = Settings(
        workspace_root=tmp_path / "workspaces",
        python_runtime_images_raw=(
            "3.10=audit-harness/python-basic:3.10,"
            "3.11=audit-harness/python-basic:3.11"
        ),
        sandbox_allowed_images_raw=(
            "audit-harness/python-basic:3.10,"
            "audit-harness/python-basic:3.11"
        ),
    )

    image = ImagePolicy(settings).for_profile("python-basic", "3.10")

    assert image == "audit-harness/python-basic:3.10"


def test_image_policy_rejects_runtime_not_in_allowed_images(tmp_path):
    settings = Settings(
        workspace_root=tmp_path / "workspaces",
        python_runtime_images_raw="3.12=audit-harness/python-basic:3.12",
        sandbox_allowed_images_raw="audit-harness/python-basic:3.11",
    )

    with pytest.raises(InvalidInputError):
        ImagePolicy(settings).for_profile("python-basic", "3.12")

