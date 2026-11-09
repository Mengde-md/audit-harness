import pytest

from app.core.exceptions import InvalidInputError
from app.planners.check_planner import ProfileCheckPlanner
from app.runners.command_policy import CommandPolicy


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf /",
        "docker run alpine",
        "cat /var/run/docker.sock",
        "shutdown now",
    ],
)
def test_blocks_dangerous_commands(command):
    with pytest.raises(InvalidInputError):
        CommandPolicy().validate(command)


def test_allows_normal_project_commands():
    policy = CommandPolicy()
    policy.validate("pytest")
    policy.validate("uvicorn app.main:app --host 0.0.0.0 --port 8000")


def test_custom_checks_override_profile_defaults():
    plan = ProfileCheckPlanner(CommandPolicy()).build_plan(
        "python-basic",
        [{"name": "python_compile", "command": "python -m compileall -q app"}],
    )

    assert plan == [
        {"name": "python_compile", "command": "python -m compileall -q app"}
    ]
