"""Regressions for the public deployment's repository and artifact boundaries."""
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.api.v1.routes.audits import get_audit_artifact_content
from app.code_review.scanner import CodeReviewScanner
from app.core.config import Settings
from app.core.exceptions import AuditNotFoundError, InvalidInputError, WorkspaceError
from app.dependency_images.detector import DependencyDetector
from app.mock_llm.env_detector import _env_files
from app.services.audit_service import AuditService
from app.services.workspace_service import WorkspaceService


@pytest.mark.parametrize("repo", [
    "/opt/clouddrive", r"C:\private\repo", "file:///etc", "ssh://github.com/a/b",
    "https://127.0.0.1/a/b", "https://github.com.evil.example/a/b",
    "https://user:secret@github.com/a/b", "https://github.com:8080/a/b",
    "https://github.com/a/../b", "https://github.com/a/%2e%2e/b",
    "https://github.com/a/b?redirect=http://127.0.0.1", "https://[bad/a/b",
])
def test_production_rejects_local_private_and_credentialed_repositories(repo):
    with pytest.raises(WorkspaceError):
        WorkspaceService(Settings(app_env="production")).validate_repo(repo)


@pytest.mark.parametrize("host", ["github.com", "gitlab.com", "gitee.com"])
def test_supported_public_repository_url(host):
    assert WorkspaceService(Settings(app_env="production")).validate_repo(f"https://{host}/team/repo.git")


@pytest.mark.asyncio
async def test_public_repository_dns_cannot_resolve_to_private_network(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [(2, 1, 6, "", ("127.0.0.1", 443))])
    with pytest.raises(WorkspaceError, match="public addresses"):
        await WorkspaceService(Settings())._validate_remote_dns("https://github.com/team/repo")


@pytest.mark.asyncio
async def test_https_clone_never_uses_shell_or_submodules(tmp_path, monkeypatch):
    service = WorkspaceService(Settings(app_env="production", workspace_root=tmp_path / "jobs"))
    calls = []
    async def fake_dns(repo):
        pass
    async def fake_git(cwd, *args):
        calls.append(args)
        if args[0] == "clone":
            workspace = Path(args[-1])
            (workspace / ".git").mkdir(parents=True)
            (workspace / "main.py").write_text("value = 1\n")
    monkeypatch.setattr(service, "_validate_remote_dns", fake_dns)
    monkeypatch.setattr(service, "_git", fake_git)
    workspace, audit_input = await service.prepare(audit_id="job-1", repo="https://github.com/team/repo",
        audit_mode="full", base_commit=None, patch=None)
    assert (workspace / "main.py").is_file()
    assert audit_input.is_dir()
    assert calls[0][:4] == ("clone", "--no-checkout", "--no-recurse-submodules", "--template=")
    assert calls[0][-3] == "--"
    assert calls[1] == ("checkout", "--force", "--detach", "HEAD", "--")


def test_repositories_have_bounded_size_and_file_count(tmp_path):
    (tmp_path / "file").write_bytes(b"abcdef")
    service = WorkspaceService(Settings())
    service.MAX_REPOSITORY_BYTES = 5
    with pytest.raises(WorkspaceError, match="limit"):
        service._validate_tree(tmp_path)


def test_manifest_cannot_read_host_files(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (tmp_path / "secret.txt").write_text("host-secret")
    with pytest.raises(InvalidInputError, match="inside"):
        DependencyDetector().detect(workspace, "python-basic", "../secret.txt")
    assert _env_files(workspace, ["../secret.txt"]) == []


@pytest.mark.asyncio
async def test_artifact_registration_does_not_allow_outside_task_reads(tmp_path):
    outside = tmp_path / "secret"
    outside.write_text("secret")
    class FakeService:
        async def get(self, audit_id):
            return SimpleNamespace(artifacts=[SimpleNamespace(artifact_type="report", path=str(outside))])
    with pytest.raises(AuditNotFoundError):
        await get_audit_artifact_content("job-1", "report", FakeService(), Settings(workspace_root=tmp_path / "jobs"))


@pytest.mark.asyncio
async def test_oversized_artifact_preview_is_rejected(tmp_path):
    task = tmp_path / "job-1"
    task.mkdir()
    report = task / "report"
    report.write_bytes(b"x" * (2 * 1024 * 1024 + 1))
    class FakeService:
        async def get(self, audit_id):
            return SimpleNamespace(artifacts=[SimpleNamespace(artifact_type="report", path=str(report))])
    with pytest.raises(InvalidInputError, match="limit"):
        await get_audit_artifact_content("job-1", "report", FakeService(), Settings(workspace_root=tmp_path))


def test_acceptance_result_read_has_a_size_limit(tmp_path):
    result = tmp_path / "result.json"
    result.write_bytes(b" " * (2 * 1024 * 1024 + 1))
    assert AuditService._read_acceptance_result(result) == {}


def test_scanner_skips_oversized_python_source(tmp_path):
    source = tmp_path / "large.py"
    source.write_bytes(b" " * (2 * 1024 * 1024 + 1))
    assert CodeReviewScanner().collect_files(tmp_path) == []
