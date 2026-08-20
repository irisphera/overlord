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

def env_flags(values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    flags: list[str] = []
    for v in values:
        flags.extend(("-e", v))
    return tuple(flags)
