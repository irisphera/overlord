from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Final

from harness import TempLauncherWorkspace
from runtime_support import runtime_workspace


SCRIPTS_DIR: Final = Path(__file__).resolve().parents[1]

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from overlord_py.web_mcp_scripts import RELEVANT_LOG_LINES_SCRIPT, VERIFY_OH_MY_OPENAGENT_SCRIPT  # noqa: E402
from overlord_py.web_restart_scripts import REQUEST_RESTART_IF_WORKSPACE_PROJECT_STALE_SCRIPT  # noqa: E402


CONNECTED_LSP_AND_AST_GREP_MCP_STATUS: Final = "\n".join(
    (
        r"{",
        r'  "lsp": {"status": "connected"},',
        r'  "ast_grep": {"status": "connected"}',
        r"}",
        "",
    )
)

PARTIAL_MCP_STATUS: Final = "\n".join(
    (
        r"{",
        r'  "websearch": {"status": "connected"}',
        r"}",
        "",
    )
)

AST_GREP_READY_LOG: Final = "\n".join(
    (
        "INFO service=plugin path=oh-my-openagent@latest loading plugin",
        "INFO service=mcp key=lsp toolCount=7 create() successfully created client",
        "INFO service=mcp key=ast_grep toolCount=2 create() successfully created client",
        "",
    )
)


class WebReadinessScriptTests(unittest.TestCase):
    def test_live_mcp_status_accepts_ast_grep_as_code_navigation(self) -> None:
        with runtime_workspace() as fixture:
            install_fake_curl(fixture.workspace)

            result = run_readiness_script(fixture.workspace.fake_bin, fixture.workspace.path, CONNECTED_LSP_AND_AST_GREP_MCP_STATUS)

            self.assertEqual(result.returncode, 0, result.stderr)

    def test_log_fallback_accepts_current_ast_grep_as_code_navigation(self) -> None:
        with runtime_workspace() as fixture:
            install_fake_curl(fixture.workspace)
            log_dir = fixture.workspace.path / "opencode-log"
            log_dir.mkdir()
            (log_dir / "current.log").write_text(AST_GREP_READY_LOG, encoding="utf-8")

            result = run_readiness_script(fixture.workspace.fake_bin, fixture.workspace.path, PARTIAL_MCP_STATUS)

            self.assertEqual(result.returncode, 0, result.stderr)

    def test_log_fallback_ignores_stale_ast_grep_readiness_logs(self) -> None:
        with runtime_workspace() as fixture:
            install_fake_curl(fixture.workspace)
            log_dir = fixture.workspace.path / "opencode-log"
            log_dir.mkdir()
            stale_log = log_dir / "stale.log"
            stale_log.write_text(AST_GREP_READY_LOG, encoding="utf-8")
            os.utime(stale_log, (1, 1))

            result = run_readiness_script(fixture.workspace.fake_bin, fixture.workspace.path, PARTIAL_MCP_STATUS)

            self.assertEqual(result.returncode, 1, result.stderr)

    def test_relevant_log_lines_skip_stale_structured_logs(self) -> None:
        with runtime_workspace() as fixture:
            log_dir = fixture.workspace.path / "opencode-log"
            log_dir.mkdir()
            stale_log = log_dir / "2026-05-13T124212.log"
            current_log = log_dir / "opencode.log"
            stale_log.write_text("INFO service=mcp key=old_stale toolCount=1 create() successfully created client\n", encoding="utf-8")
            current_log.write_text("INFO service=mcp key=lsp toolCount=7 create() successfully created client\n", encoding="utf-8")
            os.utime(stale_log, (1, 1))

            result = run_log_lines_script(fixture.workspace.path)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("key=lsp", result.stdout)
            self.assertNotIn("old_stale", result.stdout)

    def test_workspace_project_stale_script_requests_restart_when_git_cache_resolves_global(self) -> None:
        with runtime_workspace() as fixture:
            install_fake_curl(fixture.workspace)
            write_git_cache(fixture.workspace.path)

            result = run_workspace_project_stale_script(fixture.workspace.path, r'{"worktree":"/","directory":"/workspace"}')

            self.assertEqual(result.returncode, 1, result.stderr)

    def test_workspace_project_stale_script_accepts_current_project_worktree(self) -> None:
        with runtime_workspace() as fixture:
            install_fake_curl(fixture.workspace)
            write_git_cache(fixture.workspace.path)

            result = run_workspace_project_stale_script(fixture.workspace.path, r'{"worktree":"/workspace","directory":"/workspace"}')

            self.assertEqual(result.returncode, 0, result.stderr)


def install_fake_curl(workspace: TempLauncherWorkspace) -> None:
    workspace.write_executable_in_fake_bin(
        "curl",
        "\n".join(
            (
                "#!/bin/sh",
                'case "$*" in',
                '    *"/mcp"*)',
                '        if [ "${FAKE_MCP_STATUS:-0}" -ne 0 ]; then',
                '            exit "${FAKE_MCP_STATUS}"',
                "        fi",
                '        printf \'%s\\n\' "${FAKE_MCP_RESPONSE:-{}}"',
                "        ;;",
                '    *"/path?directory="*)',
                '        printf \'%s\\n\' "${FAKE_PATH_RESPONSE:-{}}"',
                "        ;;",
                "    *)",
                "        exit 0",
                "        ;;",
                "esac",
                "",
            )
        ),
    )


def write_git_cache(workspace: Path) -> None:
    git_dir = workspace / ".git"
    git_dir.mkdir()
    (git_dir / "opencode").write_text("project-cache\n", encoding="utf-8")


def run_readiness_script(fake_bin: Path, workspace: Path, mcp_status: str) -> subprocess.CompletedProcess[str]:
    log_dir = workspace / "opencode-log"
    log_file = workspace / "overlord-serve.log"
    env = dict(os.environ)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    env["FAKE_MCP_RESPONSE"] = mcp_status
    return subprocess.run(
        ["sh", "-s", "--", str(log_file), str(log_dir), "4090", "oh-my-openagent@latest"],
        cwd=workspace,
        env=env,
        input=VERIFY_OH_MY_OPENAGENT_SCRIPT,
        check=False,
        capture_output=True,
        text=True,
    )


def run_log_lines_script(workspace: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", "-s", "--", str(workspace / "overlord-serve.log"), str(workspace / "opencode-log")],
        cwd=workspace,
        input=RELEVANT_LOG_LINES_SCRIPT,
        check=False,
        capture_output=True,
        text=True,
    )


def run_workspace_project_stale_script(workspace: Path, path_response: str) -> subprocess.CompletedProcess[str]:
    pid_file = workspace / "overlord-serve.pid"
    pid_file.write_text(f"{os.getpid()}\n", encoding="utf-8")
    env = dict(os.environ)
    env["PATH"] = f"{workspace / 'fake bin'}{os.pathsep}{env.get('PATH', '')}"
    env["FAKE_PATH_RESPONSE"] = path_response
    return subprocess.run(
        ["sh", "-s", "--", str(pid_file), "4090", str(workspace)],
        cwd=workspace,
        env=env,
        input=REQUEST_RESTART_IF_WORKSPACE_PROJECT_STALE_SCRIPT,
        check=False,
        capture_output=True,
        text=True,
    )


if __name__ == "__main__":
    unittest.main()
