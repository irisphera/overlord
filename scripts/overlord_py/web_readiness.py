from __future__ import annotations

import sys
import time
from collections.abc import Mapping, Sequence

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
    for _attempt in range(max(wait_seconds, 1)):
        result = engine.run(verify_oh_my_args(paths, credential_flags), cwd=paths.workspace, env=env, input_text=VERIFY_OH_MY_OPENAGENT_SCRIPT)
        if result.returncode == 0:
            return
        if result.returncode == 2:
            break
        if wait_seconds > 1:
            time.sleep(1)
    log_result = engine.run(log_lines_args(paths), cwd=paths.workspace, env=env, input_text=RELEVANT_LOG_LINES_SCRIPT)
    detail = log_result.stderr or log_result.stdout
    _ = sys.stderr.write(readiness_warning(detail))


def readiness_warning(detail: str) -> str:
    return (
        "Warning: oh-my-openagent LSP/code navigation MCP tools are still loading.\n"
        f"Expected /mcp to report lsp plus codegraph or ast_grep as connected. OpenCode web is running, so continuing; MCP tools may finish connecting shortly.\n"
        f"Relevant OpenCode log lines:\n{detail.rstrip()}\n"
    )


def verify_oh_my_args(paths: WorkspacePaths, credential_flags: Sequence[str]) -> list[str]:
    return ["exec", "-i", *credential_flags, paths.identity.container_name, "sh", "-s", "--", OPENCODE_WEB_LOG_FILE, OPENCODE_STRUCTURED_LOG_DIR, OPENCODE_WEB_PORT, OH_MY_OPENAGENT_PACKAGE]


def log_lines_args(paths: WorkspacePaths) -> list[str]:
    return ["exec", "-i", paths.identity.container_name, "sh", "-s", "--", OPENCODE_WEB_LOG_FILE, OPENCODE_STRUCTURED_LOG_DIR]
