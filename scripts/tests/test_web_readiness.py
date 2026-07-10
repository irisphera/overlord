from __future__ import annotations

import base64
import contextlib
import io
import sys
import threading
import unittest
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Final, override
from unittest.mock import patch

from runtime_support import FakeResponse, RecordingEngine, runtime_workspace

SCRIPTS_DIR: Final = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from overlord_py.env_builder import build_environment_plan  # noqa: E402
from overlord_py.web_server import (  # noqa: E402
    WebServerError,
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

    def test_health_wait_fails_hard_when_web_returns_empty_body(self) -> None:
        with response_http_server({"/global/health": b""}) as fixture:
            with self.assertRaises(WebServerError) as caught:
                wait_for_opencode_web(str(fixture.port), wait_seconds=1)

            self.assertIn("OpenCode web UI did not become healthy", caught.exception.message)
            self.assertEqual(fixture.paths(), ["/global/health"])

    def test_root_wait_fails_hard_when_html_never_loads(self) -> None:
        with response_http_server({"/": b"ok"}) as fixture:
            with self.assertRaises(WebServerError) as caught:
                wait_for_opencode_web_ui(str(fixture.port), wait_seconds=1)

            self.assertIn("OpenCode web UI root did not become ready", caught.exception.message)
            self.assertEqual(fixture.paths(), ["/"])

    def test_mcp_readiness_uses_basic_auth_and_warns_when_tools_are_slow(self) -> None:
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

            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                verify_oh_my_openagent_loaded(engine, fixture.paths, env=fixture.runner_env, credential_flags=environment.opencode_web_credential_flags, wait_seconds=1)

            script = "\n".join(run.input_text or "" for run in engine.runs)
            self.assertIn('-u "opencode:${OPENCODE_SERVER_PASSWORD}"', script)
            self.assertIn('"http://127.0.0.1:${port}/mcp"', script)
            self.assertNotIn('"http://127.0.0.1:${port}/path?directory=/workspace"', script)
            self.assertEqual(stderr.getvalue().count("Warning: oh-my-openagent MCP readiness timed out after 1 attempt."), 1)
            self.assertEqual(stderr.getvalue().count("Relevant OpenCode log lines (diagnostic only):"), 1)
            self.assertIn("service=plugin failed", stderr.getvalue())

    def test_readiness_script_failure_status_warns_and_continues(self) -> None:
        engine = RecordingEngine(
            responses=[
                ("fetch_mcp_status", FakeResponse(returncode=2)),
                ("pattern=", FakeResponse(stdout="service=plugin oh-my-openagent failed to load\n")),
            ]
        )
        with runtime_workspace(engine=engine) as fixture:
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                verify_oh_my_openagent_loaded(engine, fixture.paths, env=fixture.runner_env, wait_seconds=1)

            self.assertEqual(stderr.getvalue().count("Warning: oh-my-openagent MCP readiness reached a terminal state."), 1)
            self.assertEqual(stderr.getvalue().count("Relevant OpenCode log lines (diagnostic only):"), 1)
            self.assertIn("service=plugin oh-my-openagent failed", stderr.getvalue())

    def test_mcp_readiness_retries_transient_result_then_succeeds(self) -> None:
        engine = RecordingEngine(
            responses=[
                ("fetch_mcp_status", FakeResponse(returncode=1, stderr="transport unavailable\n")),
                ("fetch_mcp_status", FakeResponse(returncode=0)),
            ]
        )
        with runtime_workspace(engine=engine) as fixture:
            stderr = io.StringIO()
            with patch("overlord_py.web_readiness.time.sleep") as sleep, contextlib.redirect_stderr(stderr):
                verify_oh_my_openagent_loaded(engine, fixture.paths, env=fixture.runner_env, wait_seconds=2)

            self.assertEqual(len(engine.runs), 2)
            sleep.assert_called_once_with(1)
            self.assertEqual(stderr.getvalue(), "")

    def test_mcp_readiness_timeout_warns_with_probe_and_log_diagnostics(self) -> None:
        engine = RecordingEngine(
            responses=[
                ("fetch_mcp_status", FakeResponse(returncode=1, stderr="first transport failure\n")),
                ("fetch_mcp_status", FakeResponse(returncode=1, stdout='/mcp status: {"lsp":{"status":"connected"}}\n', stderr="second probe stderr\n")),
                ("pattern=", FakeResponse(stdout="service=mcp key=codegraph still starting\n")),
            ]
        )
        with runtime_workspace(engine=engine) as fixture:
            environment = build_environment_plan(
                {"HOME": str(fixture.workspace.path / "host-home"), "OPENCODE_SERVER_PASSWORD": "secret"},
                home=fixture.workspace.path / "host-home",
                workspace_name=fixture.paths.identity.workspace_name,
            )

            stderr = io.StringIO()
            with patch("overlord_py.web_readiness.time.sleep") as sleep, contextlib.redirect_stderr(stderr):
                verify_oh_my_openagent_loaded(engine, fixture.paths, env=fixture.runner_env, credential_flags=environment.opencode_web_credential_flags, wait_seconds=2)

            script = "\n".join(run.input_text or "" for run in engine.runs)
            self.assertIn('-u "opencode:${OPENCODE_SERVER_PASSWORD}"', script)
            self.assertIn('"http://127.0.0.1:${port}/mcp"', script)
            self.assertNotIn('"http://127.0.0.1:${port}/path?directory=/workspace"', script)
            sleep.assert_called_once_with(1)
            self.assertIn("Warning: oh-my-openagent MCP readiness timed out after 2 attempts.", stderr.getvalue())
            self.assertIn('/mcp status: {"lsp":{"status":"connected"}}', stderr.getvalue())
            self.assertIn("second probe stderr", stderr.getvalue())
            self.assertIn("service=mcp key=codegraph still starting", stderr.getvalue())

    def test_mcp_readiness_terminal_status_warns_with_mcp_detail_and_stops_early(self) -> None:
        engine = RecordingEngine(
            responses=[
                ("fetch_mcp_status", FakeResponse(returncode=2, stdout="terminal stdout\n", stderr='/mcp status: {"lsp":{"status":"failed"}}\n')),
                ("pattern=", FakeResponse(stdout="service=plugin historical diagnostic\n")),
            ]
        )
        with runtime_workspace(engine=engine) as fixture:
            stderr = io.StringIO()
            with patch("overlord_py.web_readiness.time.sleep") as sleep, contextlib.redirect_stderr(stderr):
                verify_oh_my_openagent_loaded(engine, fixture.paths, env=fixture.runner_env, wait_seconds=3)

            self.assertEqual(len(engine.runs), 2)
            sleep.assert_not_called()
            self.assertIn("Warning: oh-my-openagent MCP readiness reached a terminal state.", stderr.getvalue())
            self.assertIn("terminal stdout", stderr.getvalue())
            self.assertIn('/mcp status: {"lsp":{"status":"failed"}}', stderr.getvalue())
            self.assertIn("service=plugin historical diagnostic", stderr.getvalue())


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


@contextmanager
def response_http_server(responses: Mapping[str, bytes]) -> Iterator[AuthFixture]:
    seen: list[tuple[str, str]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            seen.append((self.path, self.headers.get("Authorization", "")))
            self.send_response(200)
            self.end_headers()
            _ = self.wfile.write(responses.get(self.path, b""))

        @override
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
