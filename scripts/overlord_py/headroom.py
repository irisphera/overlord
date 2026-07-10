"""Headroom opt-in guard and private proxy lifecycle seam."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Literal

from overlord_py.engine import ContainerEngine
from overlord_py.headroom_scripts import (
    HEADROOM_PROXY_ENSURE_SCRIPT,
    HEADROOM_PROXY_STOP_SCRIPT,
    HEADROOM_PROXY_WAIT_SCRIPT,
    HEADROOM_RUNTIME_AVAILABLE_SCRIPT,
)
from overlord_py.paths import WorkspacePaths

RESPONSIBILITY: Final = "preserve Headroom fail-fast guards and latent private proxy behavior"
HEADROOM_REQUIRED_VERSION: Final = "0.27.0"
HEADROOM_INTERNAL_HOST: Final = "127.0.0.1"
HEADROOM_INTERNAL_PORT: Final = "8787"
HEADROOM_TELEMETRY_VALUE: Final = "off"
HEADROOM_PROXY_PID_FILE: Final = "/home/overlord/.local/share/opencode/headroom-proxy.pid"
HEADROOM_PROXY_LOG_FILE: Final = "/home/overlord/.local/share/opencode/headroom-proxy.log"
HEADROOM_MODE_FILE: Final = "/home/overlord/.local/share/opencode/overlord-headroom-mode"
HEADROOM_MODE_PLAIN: Final = "plain"
HEADROOM_MODE_HEADROOM: Final = "headroom"
HEADROOM_BASE_URL: Final = f"http://{HEADROOM_INTERNAL_HOST}:{HEADROOM_INTERNAL_PORT}"
HEADROOM_OPENAI_BASE_URL: Final = f"{HEADROOM_BASE_URL}/v1"
HEADROOM_HEALTH_URL: Final = f"{HEADROOM_BASE_URL}/health"
HEADROOM_RUNTIME_ENV: Final = (f"HEADROOM_TELEMETRY={HEADROOM_TELEMETRY_VALUE}",)
HEADROOM_PROXY_ARGS: Final = ("proxy", "--no-telemetry", "--host", HEADROOM_INTERNAL_HOST, "--port", HEADROOM_INTERNAL_PORT)
HEADROOM_PROXY_COMMAND_DISPLAY: Final = (
    f"HEADROOM_TELEMETRY={HEADROOM_TELEMETRY_VALUE} headroom proxy --no-telemetry "
    f"--host {HEADROOM_INTERNAL_HOST} --port {HEADROOM_INTERNAL_PORT}"
)
EXEC_USER: Final = "overlord"
HEADROOM_SCOPE_ERROR: Final = (
    "Error: --headroom and OVERLORD_HEADROOM are only supported for default, web, "
    "and opencode launches.\n"
)
HEADROOM_UNSUPPORTED_PREFIX: Final = (
    "Error: Headroom mode is currently unsupported for the selected routing/provider combination.\n"
)
HEADROOM_UNSUPPORTED_SUFFIX: Final = (
    "No checked-in Headroom routing preset/provider is currently supported without real Headroom traversal proof.\n"
    "Todo 1 only proved the generic OpenAI-compatible overlay shape with a mock; it did not prove real traversal "
    "for Azure, Google Vertex, Bedrock, LM Studio, or --lms-model overrides.\n"
    "Future support must include actual Headroom traversal evidence before this launcher may start the Headroom proxy "
    "or OpenCode in Headroom mode.\n"
)
HeadroomMode = Literal["plain", "headroom"]


@dataclass(frozen=True, slots=True)
class HeadroomScriptPlan:
    argv: tuple[str, ...]
    script: str


def describe() -> str:
    return RESPONSIBILITY


def desired_headroom_mode(headroom_enabled: bool) -> HeadroomMode:
    if headroom_enabled:
        return HEADROOM_MODE_HEADROOM
    return HEADROOM_MODE_PLAIN


def is_valid_headroom_mode(value: str) -> bool:
    match value:
        case "plain" | "headroom":
            return True
        case _:
            return False


def selected_headroom_route_status(config_name: str, config_file_name: str, lms_model: str) -> str:
    if lms_model:
        return f"--lms-model {lms_model}: dynamic lmstudio/{lms_model} runtime override is unsupported/unverified for Headroom mode."
    match config_file_name:
        case "oh-my-openagent.jsonc":
            return "--config default (oh-my-openagent.jsonc): azure/gpt-5.6-sol is unsupported/unverified for Headroom mode."
        case _:
            return f"--config {config_name} ({config_file_name}): this checked-in routing preset is unsupported/unverified for Headroom mode."


def unsupported_headroom_stderr(selected_status: str) -> str:
    return HEADROOM_UNSUPPORTED_PREFIX + f"Selected status: {selected_status}\n" + HEADROOM_UNSUPPORTED_SUFFIX


def headroom_scope_error(command_value: str) -> str:
    if command_value == "--list-configs":
        return HEADROOM_SCOPE_ERROR + "Headroom cannot be combined with --list-configs.\n"
    return HEADROOM_SCOPE_ERROR + f"Headroom cannot be combined with '{command_value}'.\n"


def plan_headroom_runtime_availability_check(
    engine: ContainerEngine,
    paths: WorkspacePaths,
    package_env: Mapping[str, str],
) -> HeadroomScriptPlan:
    return HeadroomScriptPlan(
        argv=headroom_exec_argv(engine, paths, package_env, (HEADROOM_REQUIRED_VERSION,)),
        script=HEADROOM_RUNTIME_AVAILABLE_SCRIPT,
    )


def plan_wait_for_headroom_proxy(
    engine: ContainerEngine,
    paths: WorkspacePaths,
    package_env: Mapping[str, str],
) -> HeadroomScriptPlan:
    return HeadroomScriptPlan(
        argv=headroom_exec_argv(engine, paths, package_env, (HEADROOM_HEALTH_URL, HEADROOM_PROXY_LOG_FILE)),
        script=HEADROOM_PROXY_WAIT_SCRIPT,
    )


def plan_ensure_headroom_proxy(
    engine: ContainerEngine,
    paths: WorkspacePaths,
    package_env: Mapping[str, str],
) -> HeadroomScriptPlan:
    script_args = (
        HEADROOM_PROXY_PID_FILE,
        HEADROOM_PROXY_LOG_FILE,
        HEADROOM_INTERNAL_HOST,
        HEADROOM_INTERNAL_PORT,
        HEADROOM_TELEMETRY_VALUE,
        HEADROOM_PROXY_COMMAND_DISPLAY,
        *HEADROOM_PROXY_ARGS,
    )
    return HeadroomScriptPlan(
        argv=headroom_exec_argv(engine, paths, package_env, script_args),
        script=HEADROOM_PROXY_ENSURE_SCRIPT,
    )


def plan_stop_headroom_proxy(
    engine: ContainerEngine,
    paths: WorkspacePaths,
    package_env: Mapping[str, str],
) -> HeadroomScriptPlan:
    return HeadroomScriptPlan(
        argv=headroom_exec_argv(engine, paths, package_env, (HEADROOM_PROXY_PID_FILE,)),
        script=HEADROOM_PROXY_STOP_SCRIPT,
    )


def headroom_exec_argv(
    engine: ContainerEngine,
    paths: WorkspacePaths,
    package_env: Mapping[str, str],
    script_args: tuple[str, ...],
) -> tuple[str, ...]:
    return tuple(
        engine.argv(
            [
                "exec",
                "-i",
                "-u",
                EXEC_USER,
                paths.identity.container_name,
                "env",
                "-i",
                *env_assignments(package_env),
                *HEADROOM_RUNTIME_ENV,
                "sh",
                "-s",
                "--",
                *script_args,
            ]
        )
    )


def env_assignments(env: Mapping[str, str]) -> tuple[str, ...]:
    return tuple(f"{name}={value}" for name, value in env.items())
