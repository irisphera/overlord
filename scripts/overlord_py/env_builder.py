"""Runtime environment forwarding seam."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Final

RESPONSIBILITY: Final = "plan container environment variables"
CONTAINER_HOME: Final = "/home/overlord"
OPTIONAL_TERMINAL_ENV_VARS: Final = ("COLORTERM", "TERM_PROGRAM", "TERM_PROGRAM_VERSION", "LANG", "LC_ALL")
# Azure OpenAI credentials for prime-agent's azure-openai-responses provider.
# Without AZURE_OPENAI_API_KEY the provider (and all its models, e.g. grok-4.6)
# is hidden from `prime-agent model list`.
AZURE_ENV_VARS: Final = (
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_BASE_URL",
    "AZURE_OPENAI_RESOURCE_NAME",
    "AZURE_OPENAI_API_VERSION",
    "AZURE_OPENAI_DEPLOYMENT_NAME_MAP",
)
# Legacy names exported by older setups; mapped to the AZURE_OPENAI_* names
# prime-agent reads when the modern variable is absent.
AZURE_LEGACY_ENV_ALIASES: Final = {
    "AZURE_API_KEY": "AZURE_OPENAI_API_KEY",
    "AZURE_RESOURCE_NAME": "AZURE_OPENAI_RESOURCE_NAME",
}
# Both OpenCode providers resolve credentials from OPENCODE_API_KEY. Forward the
# key explicitly because the container only mounts persisted agent state, not the
# host's ~/.prime/agent/auth.json.
OPENCODE_ENV_VARS: Final = ("OPENCODE_API_KEY",)

@dataclass(frozen=True, slots=True)
class EnvironmentPlan:
    exec_env_values: tuple[str, ...]
    exec_env_flags: tuple[str, ...]
    workspace_name: str

def describe() -> str:
    return RESPONSIBILITY

def build_environment_plan(host_env: Mapping[str, str], *, home: Path, workspace_name: str) -> EnvironmentPlan:
    normalized = normalized_host_env(host_env)
    exec_values = base_exec_env(normalized, workspace_name)
    for name in OPTIONAL_TERMINAL_ENV_VARS:
        append_present(exec_values, normalized, name)
    append_azure_env(exec_values, normalized)
    append_opencode_env(exec_values, normalized)
    return EnvironmentPlan(
        exec_env_values=tuple(exec_values),
        exec_env_flags=env_flags(exec_values),
        workspace_name=workspace_name,
    )

def normalized_host_env(host_env: Mapping[str, str]) -> dict[str, str]:
    normalized = dict(host_env)
    normalized.setdefault("DOCKER_HOST", "unix:///var/run/docker.sock")
    return normalized

def base_exec_env(host_env: Mapping[str, str], workspace_name: str) -> list[str]:
    return [
        f"HOME={CONTAINER_HOME}",
        "USER=overlord",
        "LOGNAME=overlord",
        f"XDG_CONFIG_HOME={CONTAINER_HOME}/.config",
        f"XDG_CACHE_HOME={CONTAINER_HOME}/.cache",
        f"XDG_DATA_HOME={CONTAINER_HOME}/.local/share",
        f"XDG_STATE_HOME={CONTAINER_HOME}/.local/state",
        f"TERM={host_env.get('TERM', 'xterm-256color') or 'xterm-256color'}",
        f"OVERLORD_WORKSPACE={workspace_name}",
    ]

def append_present(target: list[str], source: Mapping[str, str], name: str) -> None:
    if name in source and source[name] != "":
        target.append(f"{name}={source[name]}")

def append_azure_env(target: list[str], source: Mapping[str, str]) -> None:
    """Forward Azure OpenAI credentials, mapping legacy AZURE_* names forward.

    prime-agent reads AZURE_OPENAI_API_KEY / AZURE_OPENAI_RESOURCE_NAME /
    AZURE_OPENAI_BASE_URL. Hosts that export the older AZURE_API_KEY /
    AZURE_RESOURCE_NAME names get them mapped when the modern name is absent,
    so the azure-openai-responses provider stays visible inside the container.
    """
    for name in AZURE_ENV_VARS:
        append_present(target, source, name)
    for legacy, modern in AZURE_LEGACY_ENV_ALIASES.items():
        if modern not in source or source[modern] == "":
            if legacy in source and source[legacy] != "":
                target.append(f"{modern}={source[legacy]}")

def append_opencode_env(target: list[str], source: Mapping[str, str]) -> None:
    """Forward the shared OpenCode API key used by opencode and opencode-go."""
    for name in OPENCODE_ENV_VARS:
        append_present(target, source, name)


def env_flags(values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    flags: list[str] = []
    for v in values:
        flags.extend(("-e", v))
    return tuple(flags)
