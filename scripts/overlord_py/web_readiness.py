from __future__ import annotations

import sys
import time
from collections.abc import Mapping, Sequence

from overlord_py.engine import CommandResult
from overlord_py.packages import OH_MY_OPENAGENT_PACKAGE
from overlord_py.paths import WorkspacePaths
from overlord_py.web_scripts import RELEVANT_LOG_LINES_SCRIPT, VERIFY_OH_MY_OPENAGENT_SCRIPT
from overlord_py.web_types import EngineRunner, OPENCODE_STRUCTURED_LOG_DIR, OPENCODE_WEB_LOG_FILE, OPENCODE_WEB_PORT, OPENCODE_WEB_WAIT_SECONDS


def verify_oh_my_openagent_loaded(
    engine: EngineRunner,
    paths: WorkspacePaths,
    *,
    env: Mapping[str, str],
    credential_flags: Sequence[str] = (),
    wait_seconds: int = OPENCODE_WEB_WAIT_SECONDS,
) -> None:
    attempt_count = max(wait_seconds, 1)
    result = engine.run(verify_oh_my_args(paths, credential_flags), cwd=paths.workspace, env=env, input_text=VERIFY_OH_MY_OPENAGENT_SCRIPT)
    for attempt in range(attempt_count):
        if result.returncode == 0:
            return
        if result.returncode == 2:
            break
        if attempt + 1 == attempt_count:
            break
        if wait_seconds > 1:
            time.sleep(1)
        result = engine.run(verify_oh_my_args(paths, credential_flags), cwd=paths.workspace, env=env, input_text=VERIFY_OH_MY_OPENAGENT_SCRIPT)
    log_result = engine.run(log_lines_args(paths), cwd=paths.workspace, env=env, input_text=RELEVANT_LOG_LINES_SCRIPT)
    logs = log_result.stderr or log_result.stdout
    warning = terminal_readiness_warning(result, logs) if result.returncode == 2 else timeout_readiness_warning(attempt_count, result, logs)
    _ = sys.stderr.write(warning)


def timeout_readiness_warning(attempt_count: int, probe: CommandResult, logs: str) -> str:
    attempt_label = "attempt" if attempt_count == 1 else "attempts"
    return (
        f"Warning: oh-my-openagent MCP readiness timed out after {attempt_count} {attempt_label}.\n"
        "Expected /mcp to report both lsp and codegraph as connected. OpenCode web is running, so continuing.\n"
        f"{readiness_diagnostics(probe, logs)}"
    )


def terminal_readiness_warning(probe: CommandResult, logs: str) -> str:
    return (
        "Warning: oh-my-openagent MCP readiness reached a terminal state.\n"
        "Expected /mcp to report both lsp and codegraph as connected. OpenCode web is running, so continuing.\n"
        f"{readiness_diagnostics(probe, logs)}"
    )


def readiness_diagnostics(probe: CommandResult, logs: str) -> str:
    return (
        f"/mcp probe stdout:\n{probe.stdout.rstrip() or '(empty)'}\n"
        f"/mcp probe stderr:\n{probe.stderr.rstrip() or '(empty)'}\n"
        f"Relevant OpenCode log lines (diagnostic only):\n{logs.rstrip() or '(none)'}\n"
    )


def verify_oh_my_args(paths: WorkspacePaths, credential_flags: Sequence[str]) -> list[str]:
    return ["exec", "-i", *credential_flags, paths.identity.container_name, "sh", "-s", "--", OPENCODE_WEB_LOG_FILE, OPENCODE_STRUCTURED_LOG_DIR, OPENCODE_WEB_PORT, OH_MY_OPENAGENT_PACKAGE]


def log_lines_args(paths: WorkspacePaths) -> list[str]:
    return ["exec", "-i", paths.identity.container_name, "sh", "-s", "--", OPENCODE_WEB_LOG_FILE, OPENCODE_STRUCTURED_LOG_DIR]
