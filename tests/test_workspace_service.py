import subprocess

import pytest

from app.core.config import Settings
from app.services.workspace_service import WorkspaceService


def run_git(repo, *args):
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.mark.asyncio
async def test_prepares_repo_and_applies_patch(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    run_git(repo, "init")
    run_git(repo, "config", "user.email", "audit@example.com")
    run_git(repo, "config", "user.name", "Audit Test")
    app_file = repo / "app.py"
    app_file.write_text("value = 1\n")
    run_git(repo, "add", "app.py")
    run_git(repo, "commit", "-m", "initial")
    base_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    app_file.write_text("value = 2\n")
    patch = subprocess.run(
        ["git", "diff"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    app_file.write_text("value = 1\n")

    settings = Settings(
        workspace_root=tmp_path / "workspaces",
        sandbox_allowed_images_raw="audit-harness/python-basic:3.11",
    )
    workspace, audit_input = await WorkspaceService(settings).prepare(
        audit_id="audit-1",
        repo=str(repo),
        audit_mode="change",
        base_commit=base_commit,
        patch=patch,
    )

    assert (workspace / "app.py").read_text() == "value = 2\n"
    assert audit_input.exists()
    assert (workspace.parent / "input.patch").read_text() == patch


@pytest.mark.asyncio
async def test_prepares_full_project_without_commit(tmp_path):
    repo = tmp_path / "new-project"
    repo.mkdir()
    run_git(repo, "init")
    (repo / "main.py").write_text("print('new project')\n")

    settings = Settings(
        workspace_root=tmp_path / "workspaces",
        sandbox_allowed_images_raw="audit-harness/python-basic:3.11",
    )
    workspace, _ = await WorkspaceService(settings).prepare(
        audit_id="audit-full",
        repo=str(repo),
        audit_mode="full",
        base_commit=None,
        patch=None,
    )

    assert (workspace / "main.py").read_text() == "print('new project')\n"
