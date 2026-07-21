"""Runtime environment forwarding and credential-boundary seam."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
import shlex
from typing import Final

RESPONSIBILITY: Final = "plan container environment variables without logging credential values"
CONTAINER_HOME: Final = "/home/overlord"
CODEGRAPH_INSTALL_DIR: Final = "/home/overlord/.omo/codegraph"
CODEGRAPH_BIN: Final = "/home/overlord/.local/bin/codegraph"
CODEGRAPH_NODE_BIN: Final = "/usr/bin/node"
RUNTIME_GCLOUD_ADC_FILE: Final = "/home/overlord/.config/gcloud/application_default_credentials.json"
PROVIDER_ENV_VARS: Final = (
    "AWS_REGION",
    "AWS_BEARER_TOKEN_BEDROCK",
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_CLOUD_LOCATION",
    "AZURE_RESOURCE_NAME",
    "AZURE_API_KEY",
    "EXA_API_KEY",
    "TAVILY_API_KEY",
    "LMSTUDIO_BASE_URL",
    "LMSTUDIO_API_KEY",
    "DOCKER_HOST",
    "DOCKER_TLS_VERIFY",
    "DOCKER_CERT_PATH",
    "TESTCONTAINERS_HOST_OVERRIDE",
    "TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE",
    "TESTCONTAINERS_RYUK_DISABLED",
    "UV_CACHE_DIR",
)
MCP_CREDENTIAL_ENV_VARS: Final = ("CONTEXT7_API_KEY", "EXA_API_KEY", "TAVILY_API_KEY")
OPTIONAL_TERMINAL_ENV_VARS: Final = ("COLORTERM", "TERM_PROGRAM", "TERM_PROGRAM_VERSION", "LANG", "LC_ALL")
TITLE_HOOKS: Final = """export DISABLE_AUTO_TITLE=true
_overlord_title() { printf "\\033]0;%s\\007" "${OVERLORD_WORKSPACE:-${PWD##*/}}" }
autoload -Uz add-zsh-hook 2>/dev/null
add-zsh-hook precmd _overlord_title 2>/dev/null
add-zsh-hook preexec _overlord_title 2>/dev/null
"""


@dataclass(frozen=True, slots=True)
class EnvironmentPlan:
    exec_env_values: tuple[str, ...]
    exec_env_flags: tuple[str, ...]
    package_env: Mapping[str, str]
    opencode_web_credential_values: tuple[str, ...]
    opencode_web_credential_flags: tuple[str, ...]
    provider_env: Mapping[str, str]
    gcloud_adc_host: Path | None
    workspace_name: str

    def redacted_summary(self) -> str:
        provider_names = ",".join(sorted(self.provider_env))
        return f"env names: {provider_names}; adc={self.gcloud_adc_host is not None}; workspace={self.workspace_name}"


def describe() -> str:
    return RESPONSIBILITY


def build_environment_plan(host_env: Mapping[str, str], *, home: Path, workspace_name: str) -> EnvironmentPlan:
    normalized = normalized_host_env(host_env)
    package_env = package_environment()
    exec_values = base_exec_env(normalized, workspace_name)
    for name in OPTIONAL_TERMINAL_ENV_VARS:
        append_present(exec_values, normalized, name)
    for name in MCP_CREDENTIAL_ENV_VARS:
        append_present(exec_values, normalized, name)
    provider_values = provider_environment(normalized)
    for name in PROVIDER_ENV_VARS:
        append_present(exec_values, provider_values, name)
    gcloud_adc_host = discover_gcloud_adc(normalized, home)
    if gcloud_adc_host is not None:
        exec_values.append(f"GOOGLE_APPLICATION_CREDENTIALS={RUNTIME_GCLOUD_ADC_FILE}")
    opencode_web_values = opencode_web_credential_env(normalized)
    return EnvironmentPlan(
        exec_env_values=tuple(exec_values),
        exec_env_flags=env_flags(exec_values),
        package_env=MappingProxyType(package_env),
        opencode_web_credential_values=tuple(opencode_web_values),
        opencode_web_credential_flags=env_flags(opencode_web_values),
        provider_env=MappingProxyType(provider_values),
        gcloud_adc_host=gcloud_adc_host,
        workspace_name=workspace_name,
    )


def normalized_host_env(host_env: Mapping[str, str]) -> dict[str, str]:
    normalized = dict(host_env)
    normalized.setdefault("DOCKER_HOST", "unix:///var/run/docker.sock")
    normalized.setdefault("TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE", "/var/run/docker.sock")
    normalized.setdefault("TESTCONTAINERS_HOST_OVERRIDE", "host.docker.internal")
    normalized.setdefault("UV_CACHE_DIR", "/home/overlord/.cache/uv")
    normalized.setdefault("LMSTUDIO_BASE_URL", "http://host.docker.internal:1234/v1")
    normalized.setdefault("LMSTUDIO_API_KEY", "lm-studio")
    if not normalized.get("GOOGLE_CLOUD_LOCATION"):
        normalized["GOOGLE_CLOUD_LOCATION"] = normalized.get("VERTEX_LOCATION", "")
    if not normalized.get("GOOGLE_CLOUD_PROJECT"):
        normalized["GOOGLE_CLOUD_PROJECT"] = normalized.get("GCP_PROJECT", normalized.get("GCLOUD_PROJECT", ""))
    return normalized


def package_environment() -> dict[str, str]:
    return {
        "HOME": CONTAINER_HOME,
        "USER": "overlord",
        "LOGNAME": "overlord",
        "XDG_CONFIG_HOME": f"{CONTAINER_HOME}/.config",
        "XDG_CACHE_HOME": f"{CONTAINER_HOME}/.cache",
        "XDG_DATA_HOME": f"{CONTAINER_HOME}/.local/share",
        "XDG_STATE_HOME": f"{CONTAINER_HOME}/.local/state",
        "BUN_INSTALL": f"{CONTAINER_HOME}/.bun",
        "UV_CACHE_DIR": f"{CONTAINER_HOME}/.cache/uv",
        "npm_config_cache": f"{CONTAINER_HOME}/.npm",
        "PATH": "/usr/local/.safe-chain/shims:/usr/local/.safe-chain/bin:/home/overlord/.bun/bin:/home/overlord/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "CODEGRAPH_INSTALL_DIR": CODEGRAPH_INSTALL_DIR,
        "OMO_CODEGRAPH_BIN": CODEGRAPH_BIN,
        "CODEGRAPH_NODE_BIN": CODEGRAPH_NODE_BIN,
    }


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
        f"CODEGRAPH_INSTALL_DIR={CODEGRAPH_INSTALL_DIR}",
        f"OMO_CODEGRAPH_BIN={CODEGRAPH_BIN}",
        f"CODEGRAPH_NODE_BIN={CODEGRAPH_NODE_BIN}",
    ]


def provider_environment(host_env: Mapping[str, str]) -> dict[str, str]:
    return {name: host_env[name] for name in PROVIDER_ENV_VARS if host_env.get(name)}


def append_present(values: list[str], env: Mapping[str, str], name: str) -> None:
    value = env.get(name, "")
    if value:
        values.append(f"{name}={value}")


def env_flags(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    flags: list[str] = []
    for value in values:
        flags.extend(("-e", value))
    return tuple(flags)


def opencode_web_credential_env(host_env: Mapping[str, str]) -> list[str]:
    exa_api_key = host_env.get("EXA_API_KEY", "")
    values = ["OVERLORD_HOST_EXA_API_KEY_PRESENT=0", "EXA_API_KEY="]
    if exa_api_key:
        values = ["OVERLORD_HOST_EXA_API_KEY_PRESENT=1", f"EXA_API_KEY={exa_api_key}"]
    values.append(f"OPENCODE_SERVER_PASSWORD={host_env.get('OPENCODE_SERVER_PASSWORD', '')}")
    return values


def discover_gcloud_adc(host_env: Mapping[str, str], home: Path) -> Path | None:
    candidate = Path(host_env.get("GOOGLE_APPLICATION_CREDENTIALS", "")) if host_env.get("GOOGLE_APPLICATION_CREDENTIALS") else home / ".config" / "gcloud" / "application_default_credentials.json"
    if candidate.is_file():
        return candidate
    return None


def render_overlord_env(plan: EnvironmentPlan) -> str:
    lines: list[str] = []
    for name in PROVIDER_ENV_VARS:
        value = plan.provider_env.get(name, "")
        if value:
            lines.append(f"export {name}={quote_env(value)}")
    if plan.gcloud_adc_host is not None:
        lines.append(f"export GOOGLE_APPLICATION_CREDENTIALS={RUNTIME_GCLOUD_ADC_FILE}")
    lines.extend(
        (
            f"export CODEGRAPH_INSTALL_DIR={quote_env(CODEGRAPH_INSTALL_DIR)}",
            f"export OMO_CODEGRAPH_BIN={quote_env(CODEGRAPH_BIN)}",
            f"export CODEGRAPH_NODE_BIN={quote_env(CODEGRAPH_NODE_BIN)}",
            f"export OVERLORD_WORKSPACE={quote_env(plan.workspace_name)}",
            TITLE_HOOKS.rstrip("\n"),
        )
    )
    return "\n".join(lines) + "\n"


def quote_env(value: str) -> str:
    return shlex.quote(value)
