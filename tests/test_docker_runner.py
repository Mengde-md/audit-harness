from app.core.config import PROJECT_ROOT, Settings
from app.runners.docker_runner import DockerSandboxRunner


def test_docker_runner_uses_configured_docker_api_timeout(monkeypatch):
    captured = {}

    def fake_from_env(*, timeout):
        captured["timeout"] = timeout
        raise RuntimeError("stop before docker access")

    monkeypatch.setattr("app.runners.docker_runner.docker.from_env", fake_from_env)
    settings = Settings(docker_api_timeout_seconds=123)
    runner = DockerSandboxRunner(settings)

    try:
        runner._create_client()
    except RuntimeError:
        pass

    assert captured["timeout"] == 123


def test_setup_script_runs_commands_in_order_and_exits_on_failure():
    runner = DockerSandboxRunner(Settings())

    script = runner._build_setup_script(
        [
            "cd backend && alembic upgrade head",
            "python seed.py",
        ]
    )

    assert "AUDIT_SETUP_START index=1" in script
    assert "bash -lc 'cd backend && alembic upgrade head'" in script
    assert "AUDIT_SETUP_FAILED index=1" in script
    assert "exit $SETUP_RESULT" in script
    assert "AUDIT_SETUP_START index=2" in script


def test_container_volumes_mount_current_harness_app_readonly(tmp_path):
    runner = DockerSandboxRunner(Settings())
    workspace = tmp_path / "workspace"
    audit_input = tmp_path / "audit-input"
    workspace.mkdir()
    audit_input.mkdir()

    volumes = runner._build_container_volumes(
        workspace_path=workspace,
        audit_input_path=audit_input,
    )

    assert volumes[str(workspace.resolve())] == {
        "bind": "/workspace",
        "mode": "rw",
    }
    assert volumes[str(audit_input.resolve())] == {
        "bind": "/audit-input",
        "mode": "ro",
    }
    assert volumes[str((PROJECT_ROOT / "app").resolve())] == {
        "bind": "/opt/audit-harness/app",
        "mode": "ro",
    }
