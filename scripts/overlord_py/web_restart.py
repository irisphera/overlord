from __future__ import annotations

from collections.abc import Mapping, Sequence

from overlord_py.env_builder import CODEGRAPH_BIN, CODEGRAPH_INSTALL_DIR, CODEGRAPH_NODE_BIN
from overlord_py.headroom import HEADROOM_MODE_FILE
from overlord_py.paths import WorkspacePaths
from overlord_py.progress import StageReporter, noop_stage, stage_return_message
from overlord_py.runtime_config import CONTAINER_HOME, RestartState
from overlord_py.web_scripts import (
    REQUEST_RESTART_IF_MODE_CHANGED_SCRIPT,
    REQUEST_RESTART_IF_PLUGIN_ENV_MISSING_SCRIPT,
    REQUEST_RESTART_IF_WORKSPACE_PROJECT_STALE_SCRIPT,
    RESTART_OPENCODE_WEB_SCRIPT,
)
from overlord_py.web_types import EngineRunner, OPENCODE_WEB_HOSTNAME, OPENCODE_WEB_PID_FILE, OPENCODE_WEB_PORT, WebServerError


def request_opencode_web_restart_if_mode_changed(
    engine: EngineRunner,
    paths: WorkspacePaths,
    desired_mode: str,
    restart: RestartState,
    *,
    env: Mapping[str, str],
    stage: StageReporter = noop_stage,
) -> tuple[str, ...]:
    if restart.required:
        return ()
    stage(f"Checking OpenCode web restart need for Headroom mode in {paths.identity.container_name}...")
    result = engine.run(mode_check_args(paths, desired_mode), cwd=paths.workspace, env=env, input_text=REQUEST_RESTART_IF_MODE_CHANGED_SCRIPT)
    if result.returncode == 0:
        return ()
    restart.request()
    message = f"Restarting existing OpenCode web server because Headroom mode changed or its mode marker is missing/stale in {paths.identity.container_name}..."
    stage(message)
    return stage_return_message(stage, message)


def request_opencode_web_restart_if_plugin_env_missing(
    engine: EngineRunner,
    paths: WorkspacePaths,
    restart: RestartState,
    *,
    env: Mapping[str, str],
    credential_flags: Sequence[str],
    stage: StageReporter = noop_stage,
) -> tuple[str, ...]:
    if restart.required:
        return ()
    stage(f"Checking OpenCode web restart need for plugin environment in {paths.identity.container_name}...")
    result = engine.run(plugin_env_check_args(paths, credential_flags), cwd=paths.workspace, env=env, input_text=REQUEST_RESTART_IF_PLUGIN_ENV_MISSING_SCRIPT)
    if result.returncode == 0:
        return ()
    restart.request()
    message = f"Restarting existing OpenCode web server because its HOME/XDG/CodeGraph/MCP credential environment is not canonical in {paths.identity.container_name}..."
    stage(message)
    return stage_return_message(stage, message)


def request_opencode_web_restart_if_workspace_project_stale(
    engine: EngineRunner,
    paths: WorkspacePaths,
    restart: RestartState,
    *,
    env: Mapping[str, str],
    stage: StageReporter = noop_stage,
) -> tuple[str, ...]:
    if restart.required:
        return ()
    stage(f"Checking OpenCode web restart need for workspace project cache in {paths.identity.container_name}...")
    result = engine.run(workspace_project_stale_check_args(paths), cwd=paths.workspace, env=env, input_text=REQUEST_RESTART_IF_WORKSPACE_PROJECT_STALE_SCRIPT)
    if result.returncode == 0:
        return ()
    restart.request()
    message = (
        "Restarting existing OpenCode web server because its /workspace project cache resolved as global "
        f"even though .git/opencode is present in {paths.identity.container_name}..."
    )
    stage(message)
    return stage_return_message(stage, message)


def restart_opencode_web_if_needed(
    engine: EngineRunner,
    paths: WorkspacePaths,
    restart: RestartState,
    *,
    env: Mapping[str, str],
    stage: StageReporter = noop_stage,
) -> tuple[str, ...]:
    if not restart.required:
        return ()
    stage(f"Restarting OpenCode web server in {paths.identity.container_name}...")
    result = engine.run(["exec", "-i", paths.identity.container_name, "sh", "-s", "--", OPENCODE_WEB_PID_FILE, HEADROOM_MODE_FILE], cwd=paths.workspace, env=env, input_text=RESTART_OPENCODE_WEB_SCRIPT)
    if result.returncode != 0:
        raise WebServerError(result.stderr or result.stdout or "OpenCode web restart failed")
    restart.required = False
    message = f"Restarting OpenCode web server so package/config repairs take effect in {paths.identity.container_name}..."
    return stage_return_message(stage, message)


def mode_check_args(paths: WorkspacePaths, desired_mode: str) -> list[str]:
    return ["exec", "-i", paths.identity.container_name, "sh", "-s", "--", OPENCODE_WEB_PID_FILE, HEADROOM_MODE_FILE, desired_mode, OPENCODE_WEB_HOSTNAME, OPENCODE_WEB_PORT]


def plugin_env_check_args(paths: WorkspacePaths, credential_flags: Sequence[str]) -> list[str]:
    return ["exec", "-i", *credential_flags, paths.identity.container_name, "sh", "-s", "--", OPENCODE_WEB_PID_FILE, CONTAINER_HOME, CODEGRAPH_INSTALL_DIR, CODEGRAPH_BIN, CODEGRAPH_NODE_BIN]


def workspace_project_stale_check_args(paths: WorkspacePaths) -> list[str]:
    return ["exec", "-i", paths.identity.container_name, "sh", "-s", "--", OPENCODE_WEB_PID_FILE, OPENCODE_WEB_PORT, "/workspace"]
