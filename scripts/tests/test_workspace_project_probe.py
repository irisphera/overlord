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
DIRECT_PROBE_TIMEOUT_SECONDS: Final = "1"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from overlord_py.web_restart_scripts import REQUEST_RESTART_IF_WORKSPACE_PROJECT_STALE_SCRIPT  # noqa: E402


class WorkspaceProjectProbeTests(unittest.TestCase):
    def test_returns_healthy_when_pid_file_is_missing(self) -> None:
        with runtime_workspace() as fixture:
            write_git_cache(fixture.workspace.path)

            # Given: no OpenCode PID file.
            # When: the workspace-project probe runs.
            result = run_early_probe(fixture.workspace.path, pid_contents=None, git_cache_exists=True)

            # Then: the probe is not applicable.
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_returns_healthy_when_pid_is_dead(self) -> None:
        with runtime_workspace() as fixture:
            write_git_cache(fixture.workspace.path)

            # Given: a PID that does not identify a live process.
            # When: the workspace-project probe runs.
            result = run_early_probe(fixture.workspace.path, pid_contents="2147483647\n", git_cache_exists=True)

            # Then: the probe is not applicable.
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_returns_healthy_when_git_cache_is_missing(self) -> None:
        with runtime_workspace() as fixture:
            process = start_project_opencode_process(fixture.workspace.path)
            try:
                # Given: a live managed PID without .git/opencode.
                # When: the workspace-project probe runs.
                result = run_early_probe(fixture.workspace.path, pid_contents=f"{process.pid}\n", git_cache_exists=False)
            finally:
                stop_project_opencode_process(process)

            # Then: the probe is not applicable.
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_unrelated_live_pid_is_not_applicable_without_calling_http(self) -> None:
        with runtime_workspace() as fixture:
            install_fake_curl(fixture.workspace)
            write_git_cache(fixture.workspace.path)
            pid_file = fixture.workspace.path / "overlord-serve.pid"
            process = subprocess.Popen(
                ["/bin/sleep", "30"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                _ = pid_file.write_text(f"{process.pid}\n", encoding="utf-8")
                env = project_probe_environment(fixture.workspace.path, r'{"worktree":"/"}')

                result = subprocess.run(
                    ["sh", "-s", "--", str(pid_file), "0.0.0.0", "4090", str(fixture.workspace.path), DIRECT_PROBE_TIMEOUT_SECONDS],
                    cwd=fixture.workspace.path,
                    env=env,
                    input=REQUEST_RESTART_IF_WORKSPACE_PROJECT_STALE_SCRIPT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
            finally:
                _ = process.terminate()
                _ = process.wait(timeout=5)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((fixture.workspace.path / "curl-auth-marker").exists())

    def test_requests_restart_when_git_cache_resolves_global(self) -> None:
        with runtime_workspace() as fixture:
            install_fake_curl(fixture.workspace)
            write_git_cache(fixture.workspace.path)

            result = run_probe(fixture.workspace.path, r'{"worktree":"/","directory":"/workspace"}')

            self.assertEqual(result.returncode, 1, result.stderr)

    def test_accepts_current_project_worktree(self) -> None:
        with runtime_workspace() as fixture:
            install_fake_curl(fixture.workspace)
            write_git_cache(fixture.workspace.path)

            result = run_probe(
                fixture.workspace.path,
                json.dumps({"worktree": str(fixture.workspace.path), "directory": str(fixture.workspace.path)}),
            )

            self.assertEqual(result.returncode, 0, result.stderr)

    def test_uses_basic_auth_when_password_exists(self) -> None:
        with runtime_workspace() as fixture:
            install_fake_curl(fixture.workspace)
            write_git_cache(fixture.workspace.path)

            # Given: a password-protected OpenCode server.
            # When: the workspace-project probe requests the path endpoint.
            result = run_probe(
                fixture.workspace.path,
                json.dumps({"worktree": str(fixture.workspace.path)}),
                password="sentinel-password",
            )

            # Then: curl receives Basic Auth and the probe remains healthy.
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((fixture.workspace.path / "curl-auth-marker").read_text(encoding="utf-8"), "authenticated\n")
            self.assertNotIn("sentinel-password", result.args)
            self.assertNotIn("sentinel-password", result.stdout)
            self.assertNotIn("sentinel-password", result.stderr)

    def test_omits_auth_when_password_is_absent(self) -> None:
        with runtime_workspace() as fixture:
            install_fake_curl(fixture.workspace)
            write_git_cache(fixture.workspace.path)

            # Given: an OpenCode server without a password.
            # When: the workspace-project probe requests the path endpoint.
            result = run_probe(
                fixture.workspace.path,
                json.dumps({"worktree": str(fixture.workspace.path)}),
            )

            # Then: curl receives no Basic Auth option.
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((fixture.workspace.path / "curl-auth-marker").read_text(encoding="utf-8"), "unauthenticated\n")

    def test_returns_error_when_curl_fails(self) -> None:
        with runtime_workspace() as fixture:
            install_fake_curl(fixture.workspace)
            write_git_cache(fixture.workspace.path)

            # Given: a transport or HTTP failure from curl.
            # When: the workspace-project probe requests the path endpoint.
            result = run_probe(fixture.workspace.path, None)

            # Then: the shell reports a probe error rather than healthy or stale.
            self.assertEqual(result.returncode, 2, result.stderr)

    def test_returns_error_for_empty_response(self) -> None:
        self.assert_probe_error("")

    def test_returns_error_for_malformed_json(self) -> None:
        self.assert_probe_error('{"worktree":')

    def test_returns_error_for_missing_worktree(self) -> None:
        self.assert_probe_error(r'{"directory":"/workspace"}')

    def test_returns_error_for_non_string_worktree(self) -> None:
        self.assert_probe_error(r'{"worktree":null}')

    def test_returns_error_for_unexpected_worktree(self) -> None:
        self.assert_probe_error(r'{"worktree":"/another-project"}')

    def assert_probe_error(self, response: str) -> None:
        with runtime_workspace() as fixture:
            install_fake_curl(fixture.workspace)
            write_git_cache(fixture.workspace.path)

            result = run_probe(fixture.workspace.path, response)

            self.assertEqual(result.returncode, 2, result.stderr)


def install_fake_curl(workspace: TempLauncherWorkspace) -> None:
    workspace.write_executable_in_fake_bin(
        "curl",
        "\n".join(
            (
                "#!/bin/sh",
                'if [ "${FAKE_PATH_STATUS:-0}" -ne 0 ]; then',
                '    exit "${FAKE_PATH_STATUS}"',
                "fi",
                'curl_args="$*"',
                'timeout_found=0',
                'while [ "$#" -gt 0 ]; do',
                '    if [ "$1" = "--max-time" ]; then',
                '        [ "$#" -ge 2 ] && [ "$2" = "${FAKE_EXPECT_PATH_TIMEOUT}" ] || exit 92',
                '        timeout_found=1',
                '        shift 2',
                "    else",
                "        shift",
                "    fi",
                "done",
                '[ "$timeout_found" -eq 1 ] || exit 92',
                'case "${FAKE_EXPECT_PATH_AUTH:-0}:$curl_args" in',
                '    1:*"--user opencode:${OPENCODE_SERVER_PASSWORD}"*) printf \'authenticated\\n\' > "${FAKE_AUTH_MARKER}" ;;',
                '    0:*"--user "*) printf \'authenticated\\n\' > "${FAKE_AUTH_MARKER}"; exit 91 ;;',
                '    0:*) printf \'unauthenticated\\n\' > "${FAKE_AUTH_MARKER}" ;;',
                '    *) printf \'unauthenticated\\n\' > "${FAKE_AUTH_MARKER}"; exit 90 ;;',
                "esac",
                'printf \'%s\\n\' "${FAKE_PATH_RESPONSE-}"',
                "",
            )
        ),
    )


def write_git_cache(workspace: Path) -> None:
    git_dir = workspace / ".git"
    git_dir.mkdir()
    (git_dir / "opencode").write_text("project-cache\n", encoding="utf-8")


def run_probe(workspace: Path, path_response: str | None, password: str = "") -> subprocess.CompletedProcess[str]:
    pid_file = workspace / "overlord-serve.pid"
    process = start_project_opencode_process(workspace)
    try:
        _ = pid_file.write_text(f"{process.pid}\n", encoding="utf-8")
        return subprocess.run(
            ["sh", "-s", "--", str(pid_file), "0.0.0.0", "4090", str(workspace), DIRECT_PROBE_TIMEOUT_SECONDS],
            cwd=workspace,
            env=project_probe_environment(workspace, path_response, password),
            input=REQUEST_RESTART_IF_WORKSPACE_PROJECT_STALE_SCRIPT,
            check=False,
            capture_output=True,
            text=True,
        )
    finally:
        stop_project_opencode_process(process)


def project_probe_environment(workspace: Path, path_response: str | None, password: str = "") -> dict[str, str]:
    env = dict(os.environ)
    env["PATH"] = f"{workspace / 'fake bin'}{os.pathsep}{env.get('PATH', '')}"
    env["FAKE_EXPECT_PATH_AUTH"] = "1" if password else "0"
    env["FAKE_EXPECT_PATH_TIMEOUT"] = DIRECT_PROBE_TIMEOUT_SECONDS
    env["FAKE_AUTH_MARKER"] = str(workspace / "curl-auth-marker")
    env["FAKE_PATH_STATUS"] = "22" if path_response is None else "0"
    env["FAKE_PATH_RESPONSE"] = "" if path_response is None else path_response
    if password:
        env["OPENCODE_SERVER_PASSWORD"] = password
    else:
        env.pop("OPENCODE_SERVER_PASSWORD", None)
    return env


def start_project_opencode_process(workspace: Path) -> subprocess.Popen[bytes]:
    executable = workspace / "opencode"
    _ = executable.write_text(
        f"""#!{sys.executable}
import time

print("ready", flush=True)
while True:
    time.sleep(60)
""",
        encoding="utf-8",
    )
    _ = executable.chmod(0o755)
    process = subprocess.Popen(
        [str(executable), "serve", "--hostname", "0.0.0.0", "--port", "4090"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stdout = process.stdout
    if stdout is None:
        raise AssertionError("Disposable OpenCode process has no stdout pipe")
    if stdout.readline() != b"ready\n":
        raise AssertionError("Disposable OpenCode process did not become ready")
    return process


def stop_project_opencode_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        _ = process.terminate()
        _ = process.wait(timeout=5)
    for stream in (process.stdout, process.stderr):
        if stream is not None:
            stream.close()


def run_early_probe(workspace: Path, *, pid_contents: str | None, git_cache_exists: bool) -> subprocess.CompletedProcess[str]:
    if pid_contents is not None:
        (workspace / "overlord-serve.pid").write_text(pid_contents, encoding="utf-8")
    if git_cache_exists and not (workspace / ".git/opencode").exists():
        write_git_cache(workspace)
    return subprocess.run(
        ["sh", "-s", "--", str(workspace / "overlord-serve.pid"), "0.0.0.0", "4090", str(workspace), DIRECT_PROBE_TIMEOUT_SECONDS],
        cwd=workspace,
        env=dict(os.environ),
        input=REQUEST_RESTART_IF_WORKSPACE_PROJECT_STALE_SCRIPT,
        check=False,
        capture_output=True,
        text=True,
    )


if __name__ == "__main__":
    unittest.main()
