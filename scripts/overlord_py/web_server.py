from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Final

from overlord_py.headroom import HEADROOM_MODE_FILE
from overlord_py.paths import WorkspacePaths
from overlord_py.web_http import resolve_network_host_ip, wait_for_opencode_web, wait_for_opencode_web_ui
from overlord_py.web_proxy import ensure_host_web_proxy, stop_host_web_proxy, wait_for_host_web_proxy
from overlord_py.web_readiness import verify_oh_my_openagent_loaded
from overlord_py.web_restart import (
    request_opencode_web_restart_if_mode_changed,
    request_opencode_web_restart_if_plugin_env_missing,
    request_opencode_web_restart_if_workspace_project_stale,
    restart_opencode_web_if_needed,
)
from overlord_py.web_scripts import ENSURE_OPENCODE_WEB_SERVER_SCRIPT
from overlord_py.web_types import (
    EngineRunner,
    HostProxyResult,
    HostProxyStartPlan,
    OPENCODE_HOST_PROXY_BIND_HOST,
    OPENCODE_STRUCTURED_LOG_DIR,
    OPENCODE_WEB_HOSTNAME,
    OPENCODE_WEB_LOG_FILE,
    OPENCODE_WEB_PID_FILE,
    OPENCODE_WEB_PORT,
    OPENCODE_WEB_WAIT_SECONDS,
    WebScriptPlan,
    WebServerError,
)

_PORT_PATTERN: Final = re.compile(r":([0-9]+)$")


def resolve_published_web_port(engine: EngineRunner, paths: WorkspacePaths, *, env: Mapping[str, str]) -> str:
    result = engine.run(["port", paths.identity.container_name, f"{OPENCODE_WEB_PORT}/tcp"], cwd=paths.workspace, env=env)
    published = result.stdout.strip()
    if not published:
        raise WebServerError(
            f"Error: container {paths.identity.container_name} does not publish OpenCode web port {OPENCODE_WEB_PORT}/tcp.\n"
            "This container predates web-first launcher support. Run 'overlord fresh' once, then retry."
        )
    for line in published.splitlines():
        match = _PORT_PATTERN.search(line)
        if match is not None:
            return match.group(1)
    raise WebServerError(f"Error: could not resolve the published host port for {paths.identity.container_name}.\nRun 'overlord fresh' once, then retry.")


def plan_opencode_web_server(paths: WorkspacePaths, exec_env_flags: Sequence[str], credential_flags: Sequence[str], desired_mode: str) -> WebScriptPlan:
    return WebScriptPlan(
        argv=(
            "exec",
            "-i",
            "-w",
            "/workspace",
            "-u",
            "overlord",
            *exec_env_flags,
            *credential_flags,
            paths.identity.container_name,
            "sh",
            "-s",
            "--",
            OPENCODE_WEB_PID_FILE,
            OPENCODE_WEB_LOG_FILE,
            OPENCODE_WEB_HOSTNAME,
            OPENCODE_WEB_PORT,
            HEADROOM_MODE_FILE,
            desired_mode,
        ),
        script=ENSURE_OPENCODE_WEB_SERVER_SCRIPT,
    )


def format_access_urls(*, host_port: str, access_port: str, network_ip: str) -> str:
    lines: list[str] = []
    if access_port != host_port:
        lines.append(f"Published port: http://localhost:{host_port}")
    lines.append(f"Local access:   http://localhost:{access_port}")
    if network_ip:
        if access_port != host_port:
            lines.append(f"Published LAN:  http://{network_ip}:{host_port}")
        lines.append(f"Network access: http://{network_ip}:{access_port}")
    return "\n".join(lines) + "\n"


def resolve_access_port_for_engine(engine_name: str, paths: WorkspacePaths, *, host_port: str, env: Mapping[str, str], wait_seconds: int = OPENCODE_WEB_WAIT_SECONDS) -> str:
    if engine_name != "podman":
        return host_port
    proxy = ensure_host_web_proxy(paths, upstream_port=host_port, env=env, wait_seconds=wait_seconds)
    return host_port if proxy.access_port is None else proxy.access_port


__all__ = [
    "HostProxyResult",
    "HostProxyStartPlan",
    "OPENCODE_HOST_PROXY_BIND_HOST",
    "OPENCODE_STRUCTURED_LOG_DIR",
    "OPENCODE_WEB_LOG_FILE",
    "OPENCODE_WEB_PORT",
    "OPENCODE_WEB_WAIT_SECONDS",
    "WebServerError",
    "ensure_host_web_proxy",
    "format_access_urls",
    "plan_opencode_web_server",
    "request_opencode_web_restart_if_mode_changed",
    "request_opencode_web_restart_if_plugin_env_missing",
    "request_opencode_web_restart_if_workspace_project_stale",
    "resolve_access_port_for_engine",
    "resolve_network_host_ip",
    "resolve_published_web_port",
    "restart_opencode_web_if_needed",
    "stop_host_web_proxy",
    "verify_oh_my_openagent_loaded",
    "wait_for_host_web_proxy",
    "wait_for_opencode_web",
    "wait_for_opencode_web_ui",
]
