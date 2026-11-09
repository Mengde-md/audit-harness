import re
from pathlib import Path

KNOWN_BASE_URLS = {
    "OPENAI_BASE_URL": "openai",
    "OPENAI_API_BASE": "openai",
    "ANTHROPIC_BASE_URL": "anthropic",
    "LLM_BASE_URL": "openai",
    "LLM_API_BASE": "openai",
}
KNOWN_API_KEYS = {
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "LLM_API_KEY",
}
ENV_NAME_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")
LLM_BASE_PATTERN = re.compile(
    r"(?:OPENAI|ANTHROPIC|LLM|MODEL|AI).*?(?:BASE_URL|API_BASE)$"
)
LLM_KEY_PATTERN = re.compile(
    r"(?:OPENAI|ANTHROPIC|LLM|MODEL|AI).*?(?:API_KEY|TOKEN)$"
)


def build_mock_llm_environment(
    workspace: Path,
    *,
    port: int,
    env_files: list[str] | None = None,
    llm_adapters: list[dict] | None = None,
) -> dict[str, str]:
    root_url = f"http://127.0.0.1:{port}"
    environment = {
        name: _base_url(root_url, protocol)
        for name, protocol in KNOWN_BASE_URLS.items()
    }
    environment.update({name: "mock-audit-key" for name in KNOWN_API_KEYS})

    for adapter in llm_adapters or []:
        protocol = str(adapter.get("protocol") or "openai")
        base_url_env = adapter.get("base_url_env")
        api_key_env = adapter.get("api_key_env")
        model_env = adapter.get("model_env")
        adapter_type = str(adapter.get("type") or "chat")
        if base_url_env:
            environment[str(base_url_env)] = _base_url(root_url, protocol)
        if api_key_env:
            environment[str(api_key_env)] = "mock-audit-key"
        if model_env:
            environment[str(model_env)] = (
                "mock-embedding-model"
                if adapter_type == "embedding"
                else "mock-chat-model"
            )

    for env_file in _env_files(workspace, env_files):
        for name in _read_env_names(env_file):
            if LLM_BASE_PATTERN.search(name):
                protocol = "anthropic" if "ANTHROPIC" in name else "openai"
                environment[name] = _base_url(root_url, protocol)
            elif LLM_KEY_PATTERN.search(name):
                environment[name] = "mock-audit-key"
    return environment


def _base_url(root_url: str, protocol: str) -> str:
    return root_url if protocol == "anthropic" else f"{root_url}/v1"


def _env_files(workspace: Path, env_files: list[str] | None = None) -> list[Path]:
    candidates = (
        [workspace / item for item in env_files]
        if env_files
        else [workspace / ".env", workspace / "backend" / ".env"]
    )
    return [path for path in candidates
            if path.is_file() and path.resolve().is_relative_to(workspace.resolve())
            and not path.is_symlink() and path.stat().st_size <= 200_000]


def _read_env_names(path: Path) -> set[str]:
    names: set[str] = set()
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name = line.split("=", 1)[0].strip()
        if name.startswith("export "):
            name = name.removeprefix("export ").strip()
        if ENV_NAME_PATTERN.fullmatch(name):
            names.add(name)
    return names
