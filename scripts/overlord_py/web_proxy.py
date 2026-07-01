from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from collections.abc import Mapping

from overlord_py.paths import WorkspacePaths
from overlord_py.web_http import wait_for_http
from overlord_py.web_scripts import HOST_PROXY_SCRIPT
from overlord_py.web_types import HostProxyResult, HostProxyStartPlan, OPENCODE_HOST_PROXY_BIND_HOST, OPENCODE_WEB_WAIT_SECONDS, WebServerError


def ensure_host_web_proxy(paths: WorkspacePaths, *, upstream_port: str, env: Mapping[str, str], wait_seconds: int = OPENCODE_WEB_WAIT_SECONDS) -> HostProxyResult:
    if shutil.which("node", path=env.get("PATH")) is None:
        raise WebServerError("Error: node is required on the host to proxy OpenCode web traffic from Podman.")
    paths.state.root.mkdir(exist_ok=True)
    paths.state.host_proxy_script.write_text(HOST_PROXY_SCRIPT, encoding="utf-8")
    stop_host_web_proxy(paths)
    plan = HostProxyStartPlan(
        argv=("node", str(paths.state.host_proxy_script), upstream_port, OPENCODE_HOST_PROXY_BIND_HOST, str(paths.state.host_proxy_port_file)),
        log_file=paths.state.host_proxy_log_file,
        pid_file=paths.state.host_proxy_pid_file,
    )
    with plan.log_file.open("w", encoding="utf-8") as log_file:
        process = subprocess.Popen(list(plan.argv), cwd=paths.workspace, env=dict(env), stdout=log_file, stderr=subprocess.STDOUT)
    plan.pid_file.write_text(f"{process.pid}\n", encoding="utf-8")
    if wait_seconds <= 0:
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass
        return HostProxyResult(access_port=None, start_plan=plan)
    return HostProxyResult(access_port=wait_for_host_web_proxy(paths, wait_seconds=wait_seconds), start_plan=plan)


def stop_host_web_proxy(paths: WorkspacePaths) -> None:
    if paths.state.host_proxy_pid_file.is_file():
        pid_text = paths.state.host_proxy_pid_file.read_text(encoding="utf-8").strip()
        if pid_text.isdigit():
            try:
                os.kill(int(pid_text), signal.SIGTERM)
            except ProcessLookupError:
                pass
            except PermissionError:
                pass
    paths.state.host_proxy_pid_file.unlink(missing_ok=True)
    paths.state.host_proxy_port_file.unlink(missing_ok=True)


def wait_for_host_web_proxy(paths: WorkspacePaths, *, wait_seconds: int = OPENCODE_WEB_WAIT_SECONDS) -> str:
    for _attempt in range(max(wait_seconds, 1)):
        if paths.state.host_proxy_port_file.is_file() and paths.state.host_proxy_port_file.stat().st_size > 0:
            proxy_port = paths.state.host_proxy_port_file.read_text(encoding="utf-8").strip()
            if wait_for_http(f"http://localhost:{proxy_port}/", password="", contains="<!doctype html>", timeout=10, wait_seconds=1):
                return proxy_port
        if wait_seconds > 1:
            time.sleep(1)
    raise WebServerError(f"Error: local OpenCode host proxy did not become healthy.\nCheck {paths.state.host_proxy_log_file} for proxy startup errors.")
