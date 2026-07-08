from __future__ import annotations

import sys
import threading
import unittest
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Final

from harness import CommandRecord, HarnessRun, TempLauncherWorkspace


SCRIPTS_DIR: Final = Path(__file__).resolve().parents[1]
PYTHONPATH_ENV: Final = {"PYTHONPATH": str(SCRIPTS_DIR)}
PYTHON_ENTRYPOINT: Final = (sys.executable, "-m", "overlord_py.main")


class OrchestratorEntrypointTests(unittest.TestCase):
    def test_default_web_create_runs_bash_ordered_top_level_phases(self) -> None:
        with python_workspace(state="missing", image_exists=False) as workspace, http_fixture() as server:
            workspace.install_fake_engine("docker", state="missing", image_exists=False, port_output=server.port_output)

            result = run_python(workspace, env=host_env(workspace))

            self.assertEqual(result.returncode, 0, result.stderr)
            assert_contains_ordered(
                self,
                result.stdout.splitlines(),
                [
                    "==> Checking local Overlord image...",
                    f"==> Building overlord image from {SCRIPTS_DIR.parent}...",
                    "==> Checking container state for overlord-",
                    "==> Creating container overlord-",
                    "==> Injecting initial runtime config...",
                    "==> Checking OpenCode CLI package opencode-ai@latest in overlord-",
                    "==> Checking oh-my-openagent runtime config...",
                    "==> Checking OpenCode plugin package oh-my-openagent@4.11.1 in overlord-",
                    "==> Checking CodeGraph CLI package @colbymchenry/codegraph@1.0.1 in overlord-",
                    "==> Stopping Headroom proxy for plain OpenCode mode in overlord-",
                    "==> Checking default OpenCode skills from mattpocock/skills#v1.0.1 in overlord-",
                    "==> Restarting OpenCode web server in overlord-",
                    "==> Ensuring OpenCode web server is running in overlord-",
                    "==> Resolving published OpenCode web port for overlord-",
                    "==> Waiting for OpenCode health endpoint...",
                    "==> Waiting for OpenCode web UI...",
                    "==> Resolving local OpenCode access port...",
                    "==> Verifying oh-my-openagent MCP readiness...",
                ],
            )
            self.assertIn(f"Local access:   http://localhost:{server.port}", result.stdout)
            self.assertEqual(result.stdout.count("==> Restarting OpenCode web server in overlord-"), 1)
            docker = engine_records(workspace, "docker")
            assert_subcommands_in_order(
                self,
                docker,
                ["image", "build", "inspect", "run", "exec", "port", "exec"],
            )
            self.assertLess(index_of(docker, "run"), index_of(docker, "port"))
            self.assertTrue(any("overlord-serve.pid" in " ".join(record["argv"]) for record in docker))
            self.assertTrue(any("/home/overlord/.overlord-env" in " ".join(record["argv"]) for record in docker))

    def test_running_web_reuse_skips_create_setup_and_still_checks_runtime_before_dispatch(self) -> None:
        with python_workspace(state="running", image_exists=True) as workspace, http_fixture() as server:
            workspace.install_fake_engine("docker", state="running", image_exists=True, port_output=server.port_output)
            (workspace.path / "setup-devcontainer.sh").write_text("#!/usr/bin/env bash\nexit 99\n", encoding="utf-8")

            result = run_python(workspace, args=("web",), env=host_env(workspace))

            self.assertEqual(result.returncode, 0, result.stderr)
            docker = engine_records(workspace, "docker")
            self.assertNotIn("run", subcommands(docker))
            self.assertNotIn("start", subcommands(docker))
            self.assertFalse(any("/workspace/setup-devcontainer.sh" in record["argv"] for record in docker))
            self.assertLess(index_of(docker, "inspect"), index_of(docker, "port"))
            self.assertLess(index_of_fragment(docker, "opencode-ai"), index_of(docker, "port"))
            assert_contains_ordered(
                self,
                result.stdout.splitlines(),
                [
                    "==> Checking OpenCode web restart need for Headroom mode in overlord-",
                    "==> Checking default OpenCode skills from mattpocock/skills#v1.0.1 in overlord-",
                    "==> Checking OpenCode web restart need for plugin environment in overlord-",
                    "==> Checking OpenCode web restart need for workspace project cache in overlord-",
                ],
            )

    def test_fresh_and_purge_dispatch_before_image_build_or_runtime_repair(self) -> None:
        for command in ("fresh", "purge"):
            with self.subTest(command=command), python_workspace(state="running", image_exists=True) as workspace:
                workspace.install_fake_engine("docker", state="running", image_exists=True)
                sentinel = workspace.path / ".overlord" / "sentinel.txt"
                sentinel.parent.mkdir()
                sentinel.write_text("keep\n", encoding="utf-8")

                result = run_python(workspace, args=(command,), env=host_env(workspace))

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(sentinel.exists())
                docker = engine_records(workspace, "docker")
                self.assertNotIn("build", subcommands(docker))
                self.assertNotIn("exec", subcommands(docker))
                self.assertIn("rm", subcommands(docker))
                if command == "purge":
                    self.assertNotIn("stop", subcommands(docker))
                    self.assertTrue(any(record["argv"][1:3] == ["rm", "-f"] for record in docker))
                else:
                    self.assertIn("stop", subcommands(docker))

    def test_shell_and_zellij_final_exec_shapes_match_bash(self) -> None:
        expectations = {
            "shell": ["-it", "-w", "/workspace", "-u", "overlord", "overlord-my-project-", "zsh", "-il"],
            "zellij": ["-it", "-u", "overlord", "overlord-my-project-", "zellij", "attach", "My Project!", "--create"],
        }
        for command, expected_tail in expectations.items():
            with self.subTest(command=command), python_workspace(workspace_name="My Project!", state="running", image_exists=True) as workspace:
                workspace.install_fake_engine("docker", state="running", image_exists=True)

                result = run_python(workspace, args=(command,), env=host_env(workspace))

                self.assertEqual(result.returncode, 0, result.stderr)
                final_exec = engine_records(workspace, "docker")[-1]["argv"]
                self.assertEqual(final_exec[0:2], ["docker", "exec"])
                assert_contains_ordered(self, final_exec, expected_tail)
                self.assertIn("OVERLORD_WORKSPACE=My Project!", final_exec)
                self.assertIn("HEADROOM_TELEMETRY=off", final_exec)
                self.assertIn("==> Opening", result.stdout)
                self.assertIn("in overlord-my-project-", result.stdout)

    def test_unexpected_state_returns_nonzero_before_runtime_config_or_packages(self) -> None:
        with python_workspace(state="paused", image_exists=True) as workspace:
            workspace.install_fake_engine("docker", state="paused", image_exists=True)

            result = run_python(workspace, args=("shell",), env=host_env(workspace))

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("is in unexpected state: paused", result.stderr)
            self.assertIn("Try: overlord fresh", result.stderr)
            docker = engine_records(workspace, "docker")
            self.assertNotIn("exec", subcommands(docker))
            self.assertNotIn("run", subcommands(docker))

    def test_headroom_fail_fast_does_not_start_engine_or_proxy(self) -> None:
        with python_workspace(state="missing", image_exists=False) as workspace:
            workspace.install_fake_engine("docker", state="missing", image_exists=False)

            result = run_python(workspace, args=("--headroom",), env=host_env(workspace))

            self.assertEqual(result.returncode, 1)
            self.assertIn("Headroom mode is currently unsupported", result.stderr)
            self.assertEqual(engine_records(workspace, "docker"), [])


class HttpFixture:
    def __init__(self, server: ThreadingHTTPServer, thread: threading.Thread) -> None:
        self._server = server
        self._thread = thread
        self.port = str(server.server_port)
        self.port_output = f"0.0.0.0:{self.port}\n"

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


class ReadyHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/global/health":
            body = b"ok\n"
        else:
            body = b"<!doctype html><html><body>ok</body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        del format, args


@contextmanager
def http_fixture() -> Iterator[HttpFixture]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), ReadyHandler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    fixture = HttpFixture(server, thread)
    try:
        yield fixture
    finally:
        fixture.close()


@contextmanager
def python_workspace(
    *,
    workspace_name: str | None = None,
    state: str,
    image_exists: bool,
) -> Iterator[TempLauncherWorkspace]:
    del state, image_exists
    with TempLauncherWorkspace(workspace_name=workspace_name) as workspace:
        yield workspace


def run_python(
    workspace: TempLauncherWorkspace,
    args: tuple[str, ...] = (),
    *,
    env: Mapping[str, str],
) -> HarnessRun:
    merged_env = dict(PYTHONPATH_ENV)
    merged_env.update(env)
    return workspace.run_command((*PYTHON_ENTRYPOINT, *args), env=merged_env)


def host_env(workspace: TempLauncherWorkspace) -> dict[str, str]:
    home = workspace.path / "host-home"
    home.mkdir(exist_ok=True)
    return {"HOME": str(home), "TERM": "xterm-256color", "AZURE_API_KEY": "sentinel-azure", "EXA_API_KEY": "sentinel-exa"}


def engine_records(workspace: TempLauncherWorkspace, executable: str) -> list[CommandRecord]:
    return [record for record in workspace.read_command_log() if record["executable"] == executable]


def subcommands(records: list[CommandRecord]) -> list[str]:
    return [record["argv"][1] for record in records if len(record["argv"]) > 1]


def index_of(records: list[CommandRecord], subcommand: str) -> int:
    for index, record in enumerate(records):
        if len(record["argv"]) > 1 and record["argv"][1] == subcommand:
            return index
    raise AssertionError(f"Missing engine subcommand: {subcommand}")


def index_of_fragment(records: list[CommandRecord], fragment: str) -> int:
    for index, record in enumerate(records):
        if fragment in " ".join(record["argv"]):
            return index
    raise AssertionError(f"Missing engine argv fragment: {fragment}")


def assert_subcommands_in_order(test_case: unittest.TestCase, records: list[CommandRecord], expected: list[str]) -> None:
    cursor = 0
    commands = subcommands(records)
    for command in expected:
        cursor = commands.index(command, cursor) + 1


def assert_contains_ordered(test_case: unittest.TestCase, values: list[str], expected: list[str]) -> None:
    cursor = 0
    for item in expected:
        for index in range(cursor, len(values)):
            if item in values[index]:
                cursor = index + 1
                break
        else:
            test_case.fail(f"Missing ordered item after index {cursor}: {item}")


if __name__ == "__main__":
    unittest.main()
