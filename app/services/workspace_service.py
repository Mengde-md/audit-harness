"""Prepare bounded, link-free snapshots without executing repository code on the host."""
import asyncio
import ipaddress
import logging
import os
import re
import shutil
import socket
import stat
from pathlib import Path
from urllib.parse import unquote, urlsplit

from app.core.config import Settings
from app.core.error_codes import ErrorCode
from app.core.exceptions import WorkspaceError


class WorkspaceService:
    """Create or reuse per-audit working directories."""
    PUBLIC_GIT_HOSTS = frozenset({"github.com", "gitlab.com", "gitee.com"})
    MAX_REPOSITORY_BYTES = 256 * 1024 * 1024
    MAX_REPOSITORY_FILES = 20_000
    GIT_TIMEOUT_SECONDS = 120

    def __init__(self, settings: Settings):
        self.settings = settings

    def validate_repo(self, repo: str) -> bool:
        """Return whether this is a remote; production never reads local paths."""
        if len(repo) > 2048:
            raise WorkspaceError("repository URL exceeds the 2048 character limit")
        try:
            parsed = urlsplit(repo)
        except ValueError as exc:
            raise WorkspaceError("invalid repository URL") from exc
        # Windows drive letters are paths, not remote schemes, in local mode.
        remote = bool(parsed.scheme) and not re.match(r"^[A-Za-z]:[\\/]", repo)
        if remote:
            try:
                valid_port = parsed.port in {None, 443}
            except ValueError:
                valid_port = False
            parts = unquote(parsed.path).split("/")
            if (
                parsed.scheme != "https"
                or parsed.hostname not in self.PUBLIC_GIT_HOSTS
                or parsed.username is not None or parsed.password is not None
                or not valid_port or parsed.query or parsed.fragment
                or len([part for part in parts if part]) < 2
                or any(part in {".", ".."} for part in parts)
                or any(char in repo for char in ("\\", "\n", "\r", "\x00"))
            ):
                raise WorkspaceError("use a public HTTPS GitHub, GitLab, or Gitee repository URL")
            return True
        if self.settings.app_env.lower() in {"production", "prod"}:
            raise WorkspaceError("local repository paths are disabled in production")
        return False

    async def prepare(self, *, audit_id: str, repo: str, audit_mode: str,
                      base_commit: str | None, patch: str | None) -> tuple[Path, Path]:
        logger = logging.getLogger("audit_harness.workspace")
        remote = self.validate_repo(repo)
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", audit_id):
            raise WorkspaceError("invalid audit identifier")
        if audit_mode not in {"change", "full"}:
            raise WorkspaceError("invalid audit mode")
        if audit_mode == "change":
            if not base_commit or not patch or not re.fullmatch(r"[0-9a-fA-F]{7,64}", base_commit):
                raise WorkspaceError("change mode requires a hexadecimal base commit and patch")
        elif base_commit is not None or patch is not None:
            raise WorkspaceError("full mode requires base_commit and patch to be omitted")
        job_root = self.settings.workspace_root.resolve() / audit_id
        workspace = job_root / "workspace"
        audit_input = job_root / "audit-input"
        await asyncio.to_thread(job_root.mkdir, parents=True, exist_ok=True)
        if workspace.exists() and (workspace / ".git").is_dir():
            await asyncio.to_thread(self._validate_tree, workspace)
            await asyncio.to_thread(audit_input.mkdir, parents=True, exist_ok=True)
            return workspace, audit_input
        if workspace.exists():
            raise WorkspaceError("incomplete workspace exists; manual cleanup is required")
        logger.info("WORKSPACE_PREPARE_START | audit_id=%s | mode=%s | remote=%s", audit_id, audit_mode, remote)
        if remote:
            await self._validate_remote_dns(repo)
            await self._git(job_root, "clone", "--no-checkout", "--no-recurse-submodules",
                            "--template=", "--", repo, str(workspace))
            await asyncio.to_thread(self._validate_tree, workspace)
            await self._git(workspace, "checkout", "--force", "--detach", base_commit or "HEAD", "--")
        else:
            source = Path(repo).expanduser().resolve()
            if not source.is_dir() or not (source / ".git").is_dir():
                raise WorkspaceError("repo must be an existing local Git repository")
            await asyncio.to_thread(self._validate_tree, source)
            await asyncio.to_thread(shutil.copytree, source, workspace, symlinks=True)
            if audit_mode == "change":
                await self._git(workspace, "checkout", "--force", "--detach", base_commit, "--")
        await asyncio.to_thread(self._validate_tree, workspace)
        await asyncio.to_thread(audit_input.mkdir, parents=True, exist_ok=True)
        if audit_mode == "change":
            patch_path = job_root / "input.patch"
            await asyncio.to_thread(patch_path.write_text, patch, encoding="utf-8")
            try:
                await self._git_with_stdin(workspace, patch, "apply", "--check", "-")
                await self._git_with_stdin(workspace, patch, "apply", "-")
                await asyncio.to_thread(self._validate_tree, workspace)
            except WorkspaceError as exc:
                raise WorkspaceError(str(exc), error=ErrorCode.PATCH_APPLY_ERROR,
                                     context={"audit_id": audit_id, "base_commit": base_commit}) from exc
        logger.info("WORKSPACE_PREPARE_DONE | audit_id=%s", audit_id)
        return workspace, audit_input

    async def _validate_remote_dns(self, repo: str) -> None:
        try:
            addresses = await asyncio.to_thread(socket.getaddrinfo, urlsplit(repo).hostname, 443,
                                                type=socket.SOCK_STREAM)
            if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
                raise WorkspaceError("repository host must resolve to public addresses")
        except (socket.gaierror, ValueError) as exc:
            raise WorkspaceError("repository hostname could not be resolved safely") from exc

    def _validate_tree(self, root: Path) -> None:
        """Reject links/special files before scanners, config parsing, or host-side reads."""
        root = root.resolve()
        count = size = 0
        for directory, dirs, files in os.walk(root, followlinks=False):
            for name in dirs + files:
                path = Path(directory) / name
                try:
                    metadata = path.lstat()
                except FileNotFoundError:
                    # Git renames temporary pack/index files while cloning.
                    continue
                if stat.S_ISLNK(metadata.st_mode) or not path.resolve().is_relative_to(root):
                    raise WorkspaceError("repository symbolic links and directory junctions are not supported")
                if stat.S_ISREG(metadata.st_mode):
                    count += 1
                    size += metadata.st_size
                elif not stat.S_ISDIR(metadata.st_mode):
                    raise WorkspaceError("repository contains a special file")
                if count > self.MAX_REPOSITORY_FILES or size > self.MAX_REPOSITORY_BYTES:
                    raise WorkspaceError("repository exceeds the 256 MiB / 20000 file limit")

    @staticmethod
    def _git_environment() -> dict[str, str]:
        env = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}
        env.update({"GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_LFS_SKIP_SMUDGE": "1",
                    "GIT_ASKPASS": "", "SSH_ASKPASS": ""})
        for name in list(env):
            if name.lower() in {"http_proxy", "https_proxy", "all_proxy"}:
                env.pop(name)
        return env

    async def _git(self, cwd: Path, *args: str) -> None:
        await self._run_git(cwd, args)

    async def _git_with_stdin(self, cwd: Path, value: str, *args: str) -> None:
        await self._run_git(cwd, args, value.encode("utf-8"))

    async def _run_git(self, cwd: Path, args: tuple[str, ...], data: bytes | None = None) -> None:
        process = await asyncio.create_subprocess_exec(
            "git", "-c", f"core.hooksPath={os.devnull}",
            "-c", "protocol.allow=never", "-c", "protocol.https.allow=always",
            "-c", "http.followRedirects=false", "-c", "credential.helper=",
            "-c", "core.fsmonitor=false", "-c", "core.autocrlf=input",
            "-c", "core.eol=lf", *args, cwd=cwd, env=self._git_environment(),
            stdin=asyncio.subprocess.PIPE if data is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        communication = asyncio.create_task(process.communicate(data))
        try:
            deadline = asyncio.get_running_loop().time() + self.GIT_TIMEOUT_SECONDS
            while not communication.done():
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise WorkspaceError("Git operation exceeded the 120 second limit")
                await asyncio.wait({communication}, timeout=min(1, remaining))
                if args[0] == "clone" and Path(args[-1]).exists():
                    await asyncio.to_thread(self._validate_tree, Path(args[-1]))
            stdout, stderr = await communication
        except BaseException:
            if process.returncode is None:
                process.kill()
            await communication
            raise
        if process.returncode != 0:
            message = stderr.decode(errors="replace") or stdout.decode(errors="replace")
            raise WorkspaceError(f"Git operation failed: {message.strip()[-2000:]}")
