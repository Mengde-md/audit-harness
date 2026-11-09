"""Docker-based sandbox runner.

The runner executes project checks and API acceptance tests in isolated Docker
containers. It can also provision fixed managed services such as PostgreSQL,
pgvector, and Redis for projects that need runtime dependencies.
"""

import asyncio
import logging
import shlex
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from docker.errors import DockerException, ImageNotFound
from docker.types import LogConfig
from requests.exceptions import RequestException

import docker
from app.core.config import PROJECT_ROOT, Settings
from app.core.error_codes import ErrorCode
from app.core.exceptions import SandboxError
from app.mock_llm.env_detector import build_mock_llm_environment
from app.runners.sandbox_runner import SandboxRunner


@dataclass
class ManagedRuntime:
    """Resources created around one sandbox run and cleaned afterward."""

    network_name: str | None
    containers: list[Any]
    environment: dict[str, str]
    services: dict[str, Any]


class DockerSandboxRunner(SandboxRunner):
    """SandboxRunner implementation backed by local Docker."""

    POSTGRES_IMAGE = "postgres:16"
    POSTGRES_VECTOR_IMAGE = "pgvector/pgvector:pg16"
    REDIS_IMAGE = "redis:5.0.5"
    POSTGRES_DB = "audit_test"
    POSTGRES_USER = "audit"
    POSTGRES_PASSWORD = "audit"

    def __init__(self, settings: Settings):
        self.settings = settings

    def _create_client(self):
        """Create a Docker client with the configured API timeout."""
        return docker.from_env(timeout=self.settings.docker_api_timeout_seconds)

    async def run_command(
        self,
        *,
        audit_id: str,
        step_id: str,
        workspace_path: Path,
        command: str,
        image: str,
        timeout_seconds: int,
        audit_input_path: Path | None = None,
    ) -> dict[str, Any]:
        """Run a simple check command inside a sandbox container."""
        return await self._run_container(
            audit_id=audit_id,
            step_id=step_id,
            workspace_path=workspace_path,
            audit_input_path=audit_input_path,
            command=command,
            image=image,
            timeout_seconds=timeout_seconds,
        )

    async def run_acceptance(
        self,
        *,
        audit_id: str,
        step_id: str,
        workspace_path: Path,
        audit_input_path: Path,
        startup_command: str,
        service_port: int,
        llm_policy: str,
        env_files: list[str],
        llm_adapters: list[dict[str, Any]],
        services: dict[str, Any] | None,
        setup_commands: list[str],
        image: str,
        timeout_seconds: int,
        startup_timeout_seconds: int,
    ) -> dict[str, Any]:
        """Run the audited service plus the acceptance runner in one container."""
        quoted_startup = shlex.quote(startup_command)
        mock_start = ""
        mock_stop = ""
        environment: dict[str, str] = {}
        if llm_policy == "mock":
            # The project sees its usual env var names, but those variables point
            # to a local gateway inside the sandbox. No project code changes.
            environment = build_mock_llm_environment(
                workspace_path,
                port=self.settings.mock_llm_port,
                env_files=env_files,
                llm_adapters=llm_adapters,
            )
            mock_start = (
                "cd /opt/audit-harness && "
                "PYTHONPATH=/opt/audit-harness python -m app.mock_llm.gateway "
                f"--host 127.0.0.1 --port {self.settings.mock_llm_port} "
                "--config /audit-input/llm_mock_rules.json "
                "--trace /workspace/.audit-llm-call-traces.jsonl "
                ">/tmp/mock-llm.log 2>&1 & MOCK_LLM_PID=$!; "
                "cd /workspace; sleep 0.2; "
            )
            mock_stop = (
                "kill $MOCK_LLM_PID >/dev/null 2>&1 || true; "
                "wait $MOCK_LLM_PID >/dev/null 2>&1 || true; "
                "cat /tmp/mock-llm.log; "
            )
        setup_script = self._build_setup_script(setup_commands)
        # Keep this shell as a single script so service startup, acceptance tests,
        # log capture, and process cleanup happen in one container lifecycle.
        command = (
            "set +e; "
            f"{setup_script}"
            f"{mock_start}"
            f"bash -lc {quoted_startup} >/tmp/audit-app.log 2>&1 & "
            "APP_PID=$!; "
            "cd /opt/audit-harness && "
            "PYTHONPATH=/opt/audit-harness "
            "python -m app.api_testing.http_acceptance_runner "
            "--suite /audit-input/acceptance_suite.json "
            f"--port {service_port} "
            "--output /workspace/.audit-acceptance-result.json "
            f"--startup-timeout {startup_timeout_seconds}; "
            "RESULT=$?; "
            "cd /workspace; "
            "kill $APP_PID >/dev/null 2>&1 || true; "
            "wait $APP_PID >/dev/null 2>&1 || true; "
            f"{mock_stop}"
            "cat /tmp/audit-app.log; "
            "exit $RESULT"
        )
        return await self._run_container(
            audit_id=audit_id,
            step_id=step_id,
            workspace_path=workspace_path,
            audit_input_path=audit_input_path,
            command=command,
            image=image,
            timeout_seconds=timeout_seconds,
            environment=environment,
            services=services,
        )

    async def _run_container(
        self,
        *,
        audit_id: str,
        step_id: str,
        workspace_path: Path,
        audit_input_path: Path | None,
        command: str,
        image: str,
        timeout_seconds: int,
        environment: dict[str, str] | None = None,
        services: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create, run, collect logs from, and remove one sandbox container."""
        logger = logging.getLogger("audit_harness.sandbox")
        started = time.monotonic()
        client = None
        container = None
        runtime = ManagedRuntime(
            network_name=None,
            containers=[],
            environment={},
            services={},
        )
        timed_out = False
        try:
            logger.info(
                "SANDBOX_START | audit_id=%s | step_id=%s | image=%s "
                "| network=%s | memory=%s | cpus=%s",
                audit_id,
                step_id,
                image,
                self.settings.sandbox_network,
                self.settings.sandbox_memory,
                self.settings.sandbox_cpus,
            )
            client = await asyncio.to_thread(self._create_client)
            await asyncio.to_thread(client.images.get, image)
            runtime = await self._start_managed_runtime(
                client=client,
                audit_id=audit_id,
                step_id=step_id,
                services=services,
            )
            volumes = self._build_container_volumes(
                workspace_path=workspace_path,
                audit_input_path=audit_input_path,
            )
            container_kwargs = {
                "image": image,
                "command": ["bash", "-lc", command],
                "name": f"audit-{audit_id[:8]}-{step_id[:8]}",
                "working_dir": "/workspace",
                "volumes": volumes,
                "mem_limit": self.settings.sandbox_memory,
                "nano_cpus": int(self.settings.sandbox_cpus * 1_000_000_000),
                "user": self.settings.sandbox_user,
                "cap_drop": ["ALL"],
                "security_opt": ["no-new-privileges:true"],
                "pids_limit": 128,
                "log_config": LogConfig(type="json-file", config={"max-size": "5m", "max-file": "1"}),
                "environment": {
                    "CI": "true",
                    **runtime.environment,
                    **(environment or {}),
                },
                "labels": {
                    "audit-harness.managed": "true",
                    "audit-harness.audit-id": audit_id,
                    "audit-harness.step-id": step_id,
                },
                "detach": True,
            }
            if runtime.network_name:
                # Managed service runs need an internal bridge network; otherwise
                # use the configured sandbox network policy, usually "none".
                container_kwargs["network"] = runtime.network_name
            else:
                container_kwargs["network_mode"] = self.settings.sandbox_network
            container = await asyncio.to_thread(
                client.containers.create,
                **container_kwargs,
            )
            container_id = container.id
            await asyncio.to_thread(container.start)
            try:
                wait_result = await asyncio.wait_for(
                    asyncio.to_thread(container.wait),
                    timeout=timeout_seconds,
                )
                exit_code = int(wait_result.get("StatusCode", 1))
            except TimeoutError:
                # Timeout is represented as exit code 124, matching common shell
                # timeout conventions and simplifying downstream classification.
                timed_out = True
                exit_code = 124
                await asyncio.to_thread(container.kill)

            stdout = (
                await asyncio.to_thread(
                    container.logs,
                    stdout=True,
                    stderr=False,
                    tail=2000,
                )
            ).decode("utf-8", errors="replace")
            stderr = (
                await asyncio.to_thread(
                    container.logs,
                    stdout=False,
                    stderr=True,
                    tail=2000,
                )
            ).decode("utf-8", errors="replace")
            result = {
                "container_id": container_id,
                "image": image,
                "command": command,
                "exit_code": exit_code,
                "stdout": stdout,
                "stderr": stderr,
                "duration_ms": int((time.monotonic() - started) * 1000),
                "timed_out": timed_out,
                "managed_services": runtime.services,
            }
            logger.info(
                "SANDBOX_DONE | audit_id=%s | step_id=%s | container_id=%s "
                "| exit_code=%s | timed_out=%s | cost=%sms",
                audit_id,
                step_id,
                container_id,
                exit_code,
                timed_out,
                result["duration_ms"],
            )
            return result
        except ImageNotFound as exc:
            logger.exception(
                "SANDBOX_IMAGE_NOT_FOUND | audit_id=%s | step_id=%s | image=%s",
                audit_id,
                step_id,
                image,
            )
            raise SandboxError(
                f"sandbox image not found: {image}",
                error=ErrorCode.SANDBOX_IMAGE_NOT_FOUND,
                context={"audit_id": audit_id, "step_id": step_id, "image": image},
            ) from exc
        except (DockerException, RequestException, OSError) as exc:
            logger.exception(
                "SANDBOX_ERROR | audit_id=%s | step_id=%s | image=%s",
                audit_id,
                step_id,
                image,
            )
            raise SandboxError(
                f"Docker operation failed: {exc}",
                error=ErrorCode.DOCKER_UNAVAILABLE,
                context={
                    "audit_id": audit_id,
                    "step_id": step_id,
                    "image": image,
                    "docker_api_timeout_seconds": (
                        self.settings.docker_api_timeout_seconds
                    ),
                    "error_type": exc.__class__.__name__,
                },
            ) from exc
        finally:
            # Containers and networks are best-effort cleanup. Failure to remove
            # them should not hide the actual audit result.
            if container is not None:
                try:
                    await asyncio.to_thread(container.remove, force=True)
                except DockerException:
                    pass
            await self._cleanup_managed_runtime(runtime)
            if client is not None:
                await asyncio.to_thread(client.close)

    def _build_container_volumes(
        self,
        *,
        workspace_path: Path,
        audit_input_path: Path | None,
    ) -> dict[str, dict[str, str]]:
        """Build Docker volume mounts for one sandbox container.

        The audited project is mounted writable at ``/workspace`` because tests
        write traces and acceptance results there. The Harness application code
        is mounted read-only over the image-baked copy so Docker runs always use
        the current local runner/assertion implementation after a service restart.
        """
        volumes = {
            str(workspace_path.resolve()): {
                "bind": "/workspace",
                "mode": "rw",
            },
            str((PROJECT_ROOT / "app").resolve()): {
                "bind": "/opt/audit-harness/app",
                "mode": "ro",
            },
        }
        if audit_input_path:
            volumes[str(audit_input_path.resolve())] = {
                "bind": "/audit-input",
                "mode": "ro",
            }
        return volumes

    def _build_setup_script(self, setup_commands: list[str]) -> str:
        """Build setup command shell snippets with visible lifecycle markers."""
        if not setup_commands:
            return ""
        parts = []
        for index, setup_command in enumerate(setup_commands, start=1):
            quoted_command = shlex.quote(setup_command)
            parts.append(
                f"echo AUDIT_SETUP_START index={index}; "
                f"bash -lc {quoted_command}; "
                "SETUP_RESULT=$?; "
                "if [ $SETUP_RESULT -ne 0 ]; then "
                f"echo AUDIT_SETUP_FAILED index={index} exit_code=$SETUP_RESULT; "
                "exit $SETUP_RESULT; "
                "fi; "
                f"echo AUDIT_SETUP_DONE index={index}; "
            )
        return "".join(parts)

    async def _start_managed_runtime(
        self,
        *,
        client: Any,
        audit_id: str,
        step_id: str,
        services: dict[str, Any] | None,
    ) -> ManagedRuntime:
        """Start requested PostgreSQL/Redis services on an internal network."""
        requested = services or {}
        postgres = requested.get("postgres") or {}
        redis = requested.get("redis") or {}
        if not postgres.get("enabled") and not redis.get("enabled"):
            return ManagedRuntime(
                network_name=None,
                containers=[],
                environment={},
                services={},
            )

        logger = logging.getLogger("audit_harness.sandbox")
        network_name = f"audit-{audit_id[:8]}-{step_id[:8]}-net"
        network = await asyncio.to_thread(
            client.networks.create,
            network_name,
            driver="bridge",
            internal=True,
            labels={
                "audit-harness.managed": "true",
                "audit-harness.audit-id": audit_id,
                "audit-harness.step-id": step_id,
            },
        )
        containers: list[Any] = []
        environment: dict[str, str] = {}
        started_services: dict[str, Any] = {}
        try:
            if postgres.get("enabled"):
                variant = postgres.get("variant", "pg16")
                image = (
                    self.POSTGRES_VECTOR_IMAGE
                    if variant == "pg16-vector"
                    else self.POSTGRES_IMAGE
                )
                await asyncio.to_thread(client.images.get, image)
                name = f"audit-{audit_id[:8]}-{step_id[:8]}-postgres"
                container = await asyncio.to_thread(
                    client.containers.create,
                    image=image,
                    name=name,
                    environment={
                        "POSTGRES_DB": self.POSTGRES_DB,
                        "POSTGRES_USER": self.POSTGRES_USER,
                        "POSTGRES_PASSWORD": self.POSTGRES_PASSWORD,
                    },
                    network=network_name,
                    mem_limit="512m",
                    nano_cpus=1_000_000_000,
                    pids_limit=128,
                    security_opt=["no-new-privileges:true"],
                    log_config=LogConfig(type="json-file", config={"max-size": "5m", "max-file": "1"}),
                    labels={
                        "audit-harness.managed": "true",
                        "audit-harness.audit-id": audit_id,
                        "audit-harness.step-id": step_id,
                        "audit-harness.service": "postgres",
                    },
                    detach=True,
                )
                containers.append(container)
                await asyncio.to_thread(container.start)
                await self._wait_for_exec(
                    container,
                    ["pg_isready", "-U", self.POSTGRES_USER, "-d", self.POSTGRES_DB],
                    timeout_seconds=self.settings.service_startup_timeout_seconds,
                )
                environment["DATABASE_URL"] = (
                    f"postgresql+psycopg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
                    f"@{name}:5432/{self.POSTGRES_DB}"
                )
                # Standard env names let most Python web projects run migrations
                # without requiring project-specific Docker Compose files.
                started_services["postgres"] = {
                    "enabled": True,
                    "variant": variant,
                    "image": image,
                    "database": self.POSTGRES_DB,
                    "username": self.POSTGRES_USER,
                    "host": name,
                    "port": 5432,
                }

            if redis.get("enabled"):
                await asyncio.to_thread(client.images.get, self.REDIS_IMAGE)
                name = f"audit-{audit_id[:8]}-{step_id[:8]}-redis"
                container = await asyncio.to_thread(
                    client.containers.create,
                    image=self.REDIS_IMAGE,
                    name=name,
                    network=network_name,
                    mem_limit="128m",
                    nano_cpus=500_000_000,
                    pids_limit=64,
                    security_opt=["no-new-privileges:true"],
                    log_config=LogConfig(type="json-file", config={"max-size": "5m", "max-file": "1"}),
                    labels={
                        "audit-harness.managed": "true",
                        "audit-harness.audit-id": audit_id,
                        "audit-harness.step-id": step_id,
                        "audit-harness.service": "redis",
                    },
                    detach=True,
                )
                containers.append(container)
                await asyncio.to_thread(container.start)
                await self._wait_for_exec(
                    container,
                    ["redis-cli", "ping"],
                    expected_text="PONG",
                    timeout_seconds=self.settings.service_startup_timeout_seconds,
                )
                environment["REDIS_URL"] = f"redis://{name}:6379/0"
                started_services["redis"] = {
                    "enabled": True,
                    "image": self.REDIS_IMAGE,
                    "host": name,
                    "port": 6379,
                    "database": 0,
                }

            logger.info(
                "SANDBOX_SERVICES_READY | audit_id=%s | step_id=%s | services=%s",
                audit_id,
                step_id,
                ",".join(started_services),
            )
            return ManagedRuntime(
                network_name=network_name,
                containers=containers,
                environment=environment,
                services=started_services,
            )
        except Exception:
            # If any managed service fails to start, tear down the partial runtime
            # before surfacing the sandbox error to the audit step.
            await self._cleanup_managed_runtime(
                ManagedRuntime(
                    network_name=network_name,
                    containers=containers,
                    environment=environment,
                    services=started_services,
                )
            )
            try:
                await asyncio.to_thread(network.remove)
            except DockerException:
                pass
            raise

    async def _wait_for_exec(
        self,
        container: Any,
        command: list[str],
        *,
        timeout_seconds: int,
        expected_text: str | None = None,
    ) -> None:
        """Poll a command inside a service container until it is ready."""
        deadline = time.monotonic() + timeout_seconds
        last_output = ""
        while time.monotonic() < deadline:
            result = await asyncio.to_thread(container.exec_run, command)
            output = result.output.decode("utf-8", errors="replace")
            last_output = output.strip()
            if result.exit_code == 0 and (
                expected_text is None or expected_text in output
            ):
                return
            await asyncio.sleep(1)
        raise SandboxError(
            f"managed service did not become ready: {last_output}",
            error=ErrorCode.SANDBOX_ERROR,
            context={"command": " ".join(command), "last_output": last_output},
        )

    async def _cleanup_managed_runtime(self, runtime: ManagedRuntime) -> None:
        """Remove managed containers and network after a sandbox run."""
        for container in reversed(runtime.containers):
            try:
                await asyncio.to_thread(container.remove, force=True)
            except DockerException:
                pass
        if runtime.network_name:
            try:
                client = await asyncio.to_thread(docker.from_env)
                network = await asyncio.to_thread(
                    client.networks.get,
                    runtime.network_name,
                )
                await asyncio.to_thread(network.remove)
            except DockerException:
                pass
