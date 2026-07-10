from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Final

from harness import TempLauncherWorkspace
from runtime_support import runtime_workspace


SCRIPTS_DIR: Final = Path(__file__).resolve().parents[1]
TERMINAL_MCP_STATUSES: Final = ("failed", "disabled", "needs_auth", "needs_client_registration")

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from overlord_py.web_mcp_scripts import RELEVANT_LOG_LINES_SCRIPT, VERIFY_OH_MY_OPENAGENT_SCRIPT  # noqa: E402


CONNECTED_LSP_AND_AST_GREP_MCP_STATUS: Final = "\n".join(
    (
        r"{",
        r'  "lsp": {"status": "connected"},',
        r'  "ast_grep": {"status": "connected"}',
        r"}",
        "",
    )
)

CONNECTED_LSP_AND_CODEGRAPH_MCP_STATUS: Final = "\n".join(
    (
        r"{",
        r'  "lsp": {',
        r'    "status": "connected",',
        r'    "tools": {"hover": true}',
        r"  },",
        r'  "codegraph": {',
        r'    "status": "connected",',
        r'    "metadata": {"transport": "stdio"}',
        r"  }",
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

HISTORICAL_PLUGIN_FAILURE_LOG: Final = "INFO service=plugin path=oh-my-openagent@4.16.0 failed to load\n"


class WebReadinessScriptTests(unittest.TestCase):
    def test_live_mcp_status_accepts_nested_multiline_lsp_and_codegraph(self) -> None:
        with runtime_workspace() as fixture:
            install_fake_curl(fixture.workspace)

            result = run_readiness_script(fixture.workspace.fake_bin, fixture.workspace.path, CONNECTED_LSP_AND_CODEGRAPH_MCP_STATUS)

            self.assertEqual(result.returncode, 0, result.stderr)

    def test_required_mcp_terminal_statuses_return_terminal_exit_code(self) -> None:
        with runtime_workspace() as fixture:
            install_fake_curl(fixture.workspace)

            for mcp_name in ("lsp", "codegraph"):
                for status in TERMINAL_MCP_STATUSES:
                    with self.subTest(mcp_name=mcp_name, status=status):
                        mcp_status = {
                            "lsp": {"status": "connected"},
                            "codegraph": {"status": "connected"},
                        }
                        mcp_status[mcp_name] = {"status": status, "error": f"{mcp_name} is {status}"}

                        result = run_readiness_script(fixture.workspace.fake_bin, fixture.workspace.path, json.dumps(mcp_status))

                        self.assertEqual(result.returncode, 2, result.stderr)
                        self.assertIn(f'"status": "{status}"', result.stderr)

    def test_live_mcp_status_accepts_lsp_and_codegraph_without_requiring_ast_grep(self) -> None:
        with runtime_workspace() as fixture:
            install_fake_curl(fixture.workspace)
            mcp_status = json.dumps(
                {
                    "lsp": {"status": "connected"},
                    "codegraph": {"status": "connected"},
                    "ast_grep": {"status": "failed"},
                }
            )

            result = run_readiness_script(fixture.workspace.fake_bin, fixture.workspace.path, mcp_status)

            self.assertEqual(result.returncode, 0, result.stderr)

    def test_ast_grep_without_codegraph_is_not_sufficient(self) -> None:
        with runtime_workspace() as fixture:
            install_fake_curl(fixture.workspace)

            result = run_readiness_script(fixture.workspace.fake_bin, fixture.workspace.path, CONNECTED_LSP_AND_AST_GREP_MCP_STATUS)

            self.assertEqual(result.returncode, 1, result.stderr)

    def test_recent_historical_success_log_cannot_decide_current_readiness(self) -> None:
        with runtime_workspace() as fixture:
            install_fake_curl(fixture.workspace)
            log_dir = fixture.workspace.path / "opencode-log"
            log_dir.mkdir()
            (log_dir / "current.log").write_text(AST_GREP_READY_LOG, encoding="utf-8")

            result = run_readiness_script(fixture.workspace.fake_bin, fixture.workspace.path, PARTIAL_MCP_STATUS)

            self.assertEqual(result.returncode, 1, result.stderr)

    def test_recent_historical_failure_log_is_not_terminal(self) -> None:
        with runtime_workspace() as fixture:
            install_fake_curl(fixture.workspace)
            log_dir = fixture.workspace.path / "opencode-log"
            log_dir.mkdir()
            (log_dir / "current.log").write_text(HISTORICAL_PLUGIN_FAILURE_LOG, encoding="utf-8")

            result = run_readiness_script(fixture.workspace.fake_bin, fixture.workspace.path, PARTIAL_MCP_STATUS)

            self.assertEqual(result.returncode, 1, result.stderr)

    def test_malformed_mcp_json_is_transient(self) -> None:
        with runtime_workspace() as fixture:
            install_fake_curl(fixture.workspace)

            result = run_readiness_script(fixture.workspace.fake_bin, fixture.workspace.path, '{"lsp":')

            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn("Malformed /mcp response", result.stderr)

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
                '        if [ "${FAKE_MCP_RESPONSE+x}" = x ]; then',
                '            printf \'%s\\n\' "${FAKE_MCP_RESPONSE}"',
                "        else",
                "            printf '{}\\n'",
                "        fi",
                "        ;;",
                "    *)",
                "        exit 0",
                "        ;;",
                "esac",
                "",
            )
        ),
    )


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


if __name__ == "__main__":
    unittest.main()
