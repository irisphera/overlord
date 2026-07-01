from __future__ import annotations

import base64
import os
import sys
import threading
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Final

from runtime_support import FakeResponse, RecordingEngine, runtime_workspace


SCRIPTS_DIR: Final = Path(__file__).resolve().parents[1]

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from overlord_py.env_builder import build_environment_plan, package_environment  # noqa: E402
from overlord_py.paths import build_workspace_paths  # noqa: E402
from overlord_py.runtime_config import RestartState  # noqa: E402
from overlord_py.web_server import (  # noqa: E402
    OPENCODE_HOST_PROXY_BIND_HOST,
    OPENCODE_WEB_LOG_FILE,
    OPENCODE_WEB_PORT,
    WebServerError,
    ensure_host_web_proxy,
    format_access_urls,
    plan_opencode_web_server,
    request_opencode_web_restart_if_mode_changed,
    request_opencode_web_restart_if_plugin_env_missing,
    resolve_published_web_port,
    restart_opencode_web_if_needed,
    stop_host_web_proxy,
    verify_oh_my_openagent_loaded,
    wait_for_opencode_web,
    wait_for_opencode_web_ui,
)


class WebReadinessTests(unittest.TestCase):
    def test_health_and_root_html_waits_use_basic_auth_when_password_is_set(self) -> None:
        with auth_http_server() as fixture:
            wait_for_opencode_web(str(fixture.port), password="secret", wait_seconds=1)
            wait_for_opencode_web_ui(str(fixture.port), password="secret", wait_seconds=1)

            self.assertEqual(fixture.paths(), ["/global/health", "/"])
            self.assertEqual(fixture.auth_headers(), [basic_auth("opencode", "secret"), basic_auth("opencode", "secret")])

    def test_health_wait_retries_when_server_closes_connection_without_response(self) -> None:
        with close_once_http_server() as fixture:
            wait_for_opencode_web(str(fixture.port), wait_seconds=2)

            self.assertEqual(fixture.paths(), ["/global/health", "/global/health"])

    def test_mcp_and_path_readiness_script_uses_basic_auth_and_log_fallback(self) -> None:
        engine = RecordingEngine(
            responses=[
                ("fetch_mcp_status", FakeResponse(returncode=1)),
                ("pattern=", FakeResponse(stdout="service=plugin failed to load\n")),
            ]
        )
        with runtime_workspace(engine=engine) as fixture:
            environment = build_environment_plan(
                {"HOME": str(fixture.workspace.path / "host-home"), "OPENCODE_SERVER_PASSWORD": "secret"},
                home=fixture.workspace.path / "host-home",
                workspace_name=fixture.paths.identity.workspace_name,
            )

            with self.assertRaises(WebServerError) as caught:
                verify_oh_my_openagent_loaded(engine, fixture.paths, env=fixture.runner_env, credential_flags=environment.opencode_web_credential_flags, wait_seconds=1)

            script = "\n".join(run.input_text or "" for run in engine.runs)
            self.assertIn('-u "opencode:${OPENCODE_SERVER_PASSWORD}"', script)
            self.assertIn('"http://127.0.0.1:${port}/mcp"', script)
            self.assertIn('"http://127.0.0.1:${port}/path?directory=/workspace"', script)
            self.assertEqual(caught.exception.message.count("Relevant OpenCode log lines:"), 1)
            self.assertIn("Relevant OpenCode log lines:", caught.exception.message)
            self.assertIn("service=plugin failed", caught.exception.message)


class WebPlanningTests(unittest.TestCase):
    def test_empty_engine_port_output_reports_current_fresh_diagnostic(self) -> None:
        engine = RecordingEngine(responses=[("port", FakeResponse(returncode=1, stdout=""))])
        with runtime_workspace(engine=engine) as fixture:
            with self.assertRaises(WebServerError) as caught:
                resolve_published_web_port(engine, fixture.paths, env=fixture.runner_env)

            self.assertEqual(caught.exception.status, 1)
            self.assertIn(f"Error: container {fixture.paths.identity.container_name} does not publish OpenCode web port {OPENCODE_WEB_PORT}/tcp.", caught.exception.message)
            self.assertIn("Run 'overlord fresh' once", caught.exception.message)

    def test_published_port_parses_last_colon_number(self) -> None:
        engine = RecordingEngine(responses=[("port", FakeResponse(stdout="0.0.0.0:49152\n[::]:49153\n"))])
        with runtime_workspace(engine=engine) as fixture:
            self.assertEqual(resolve_published_web_port(engine, fixture.paths, env=fixture.runner_env), "49152")

    def test_podman_writes_node_proxy_script_and_starts_host_node(self) -> None:
        with runtime_workspace() as fixture:
            fixture.workspace.install_fake_command("node")
            result = ensure_host_web_proxy(
                fixture.paths,
                upstream_port="49152",
                env={"PATH": f"{fixture.workspace.fake_bin}{os.pathsep}{os.environ.get('PATH', '')}"},
                wait_seconds=0,
            )

            self.assertEqual(result.start_plan.argv[0], "node")
            self.assertEqual(result.start_plan.argv[1], str(fixture.paths.state.host_proxy_script))
            self.assertEqual(result.start_plan.argv[2:], ("49152", OPENCODE_HOST_PROXY_BIND_HOST, str(fixture.paths.state.host_proxy_port_file)))
            self.assertTrue(fixture.paths.state.host_proxy_script.exists())
            self.assertIn('const http = require("http")', fixture.paths.state.host_proxy_script.read_text(encoding="utf-8"))
            self.assertTrue(fixture.paths.state.host_proxy_pid_file.exists())
            self.assertFalse(fixture.paths.state.host_proxy_port_file.exists())

    def test_docker_url_output_skips_proxy_labels_while_podman_includes_published_labels(self) -> None:
        docker_output = format_access_urls(host_port="49152", access_port="49152", network_ip="10.0.0.5")
        podman_output = format_access_urls(host_port="49152", access_port="40231", network_ip="10.0.0.5")

        self.assertEqual(docker_output, "Local access:   http://localhost:49152\nNetwork access: http://10.0.0.5:49152\n")
        self.assertEqual(
            podman_output,
            "Published port: http://localhost:49152\nLocal access:   http://localhost:40231\nPublished LAN:  http://10.0.0.5:49152\nNetwork access: http://10.0.0.5:40231\n",
        )

    def test_restart_and_server_plans_preserve_bash_commands_and_mode_marker(self) -> None:
        engine = RecordingEngine(responses=[("is_expected_opencode_cmdline", FakeResponse(returncode=1))])
        with runtime_workspace(engine=engine) as fixture:
            restart = RestartState()
            messages = request_opencode_web_restart_if_mode_changed(engine, fixture.paths, "plain", restart, env=fixture.runner_env)
            restart_messages = restart_opencode_web_if_needed(engine, fixture.paths, restart, env=fixture.runner_env)
            environment = build_environment_plan(
                {"HOME": str(fixture.workspace.path / "host-home"), "EXA_API_KEY": "sentinel-exa"},
                home=fixture.workspace.path / "host-home",
                workspace_name=fixture.paths.identity.workspace_name,
            )
            plugin_messages = request_opencode_web_restart_if_plugin_env_missing(
                engine,
                fixture.paths,
                restart,
                env=fixture.runner_env,
                credential_flags=environment.opencode_web_credential_flags,
            )
            plan = plan_opencode_web_server(fixture.paths, environment.exec_env_flags, environment.opencode_web_credential_flags, "plain")

            self.assertTrue(restart.required)
            self.assertIn("Headroom mode changed", messages[0])
            self.assertIn("Restarting OpenCode web server so package/config repairs take effect", restart_messages[0])
            self.assertEqual(plugin_messages, ())
            self.assertIn("opencode serve --hostname", plan.script)
            self.assertIn("printf '%s\\n' \"${DESIRED_MODE}\" >\"${MODE_FILE}\"", plan.script)
            self.assertIn(OPENCODE_WEB_LOG_FILE, plan.argv)

    def test_stop_host_web_proxy_removes_cached_files(self) -> None:
        with runtime_workspace() as fixture:
            fixture.paths.state.root.mkdir()
            fixture.paths.state.host_proxy_pid_file.write_text("999999\n", encoding="utf-8")
            fixture.paths.state.host_proxy_port_file.write_text("40000\n", encoding="utf-8")

            stop_host_web_proxy(fixture.paths)

            self.assertFalse(fixture.paths.state.host_proxy_pid_file.exists())
            self.assertFalse(fixture.paths.state.host_proxy_port_file.exists())


class AuthFixture:
    def __init__(self, server: ThreadingHTTPServer, thread: threading.Thread, seen: list[tuple[str, str]]) -> None:
        self.server = server
        self.thread = thread
        self.seen = seen
        self.port = server.server_port

    def paths(self) -> list[str]:
        return [path for path, _auth in self.seen]

    def auth_headers(self) -> list[str]:
        return [auth for _path, auth in self.seen]


@contextmanager
def auth_http_server() -> Iterator[AuthFixture]:
    seen: list[tuple[str, str]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            seen.append((self.path, self.headers.get("Authorization", "")))
            self.send_response(200)
            self.end_headers()
            if self.path == "/":
                self.wfile.write(b"<!doctype html><title>OpenCode</title>")
            else:
                self.wfile.write(b"ok")

        def log_message(self, format: str, *args: object) -> None:
            del format, args

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield AuthFixture(server, thread, seen)
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


@contextmanager
def close_once_http_server() -> Iterator[AuthFixture]:
    seen: list[tuple[str, str]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            seen.append((self.path, self.headers.get("Authorization", "")))
            if len(seen) == 1:
                self.close_connection = True
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, format: str, *args: object) -> None:
            del format, args

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield AuthFixture(server, thread, seen)
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def basic_auth(user: str, password: str) -> str:
    token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


if __name__ == "__main__":
    unittest.main()
