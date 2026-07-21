from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from typing import Final

SCRIPTS_DIR: Final = Path(__file__).resolve().parents[1]

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from scripts.overlord_py.env_builder import build_environment_plan  # noqa: E402
from scripts.overlord_py.runtime_config import RestartState  # noqa: E402
from scripts.overlord_py.web_server import (  # noqa: E402
    OPENCODE_HOST_PROXY_BIND_HOST,
    OPENCODE_WEB_LOG_FILE,
    OPENCODE_WEB_PORT,
    WebServerError,
    ensure_host_web_proxy,
    format_access_urls,
    plan_opencode_web_server,
    request_opencode_web_restart_if_plugin_env_missing,
    resolve_published_web_port,
    restart_opencode_web_if_needed,
    stop_host_web_proxy,
)
from scripts.tests.runtime_support import FakeResponse, RecordingEngine, runtime_workspace  # noqa: E402




class WebPlanningTests(unittest.TestCase):
    def test_web_plan_has_one_exact_opencode_password_assignment(self) -> None:
        with runtime_workspace() as fixture:
            environment = build_environment_plan(
                {"OPENCODE_SERVER_PASSWORD": "web-secret"},
                home=fixture.workspace.path / "host-home",
                workspace_name=fixture.paths.identity.workspace_name,
            )

            plan = plan_opencode_web_server(
                fixture.paths,
                environment.exec_env_flags,
                environment.opencode_web_credential_flags,
            )

        container_index = plan.argv.index(fixture.paths.identity.container_name)
        self.assertEqual(plan.argv[container_index - 2 : container_index], ("-e", "OPENCODE_SERVER_PASSWORD=web-secret"))
        self.assertEqual(plan.argv.count("OPENCODE_SERVER_PASSWORD=web-secret"), 1)

    def test_empty_engine_port_output_reports_current_fresh_diagnostic(self) -> None:
        engine = RecordingEngine(responses=[("port", FakeResponse(returncode=1, stdout=""))])
        with runtime_workspace(engine=engine) as fixture:
            with self.assertRaises(WebServerError) as caught:
                _ = resolve_published_web_port(engine, fixture.paths, env=fixture.runner_env)

            self.assertEqual(caught.exception.status, 1)
            self.assertIn(f"Error: container {fixture.paths.identity.container_name} does not publish OpenCode web port {OPENCODE_WEB_PORT}/tcp.", caught.exception.message)
            self.assertIn("Run 'overlord fresh' once", caught.exception.message)

    def test_published_port_parses_last_colon_number(self) -> None:
        engine = RecordingEngine(responses=[("port", FakeResponse(stdout="0.0.0.0:49152\n[::]:49153\n"))])
        with runtime_workspace(engine=engine) as fixture:
            self.assertEqual(resolve_published_web_port(engine, fixture.paths, env=fixture.runner_env), "49152")

    def test_podman_writes_node_proxy_script_and_starts_host_node(self) -> None:
        with runtime_workspace() as fixture:
            _ = fixture.workspace.install_fake_command("node")
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

    def test_restart_and_server_plans_preserve_bash_commands(self) -> None:
        engine = RecordingEngine()
        with runtime_workspace(engine=engine) as fixture:
            restart = RestartState(required=True)
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
            plan = plan_opencode_web_server(fixture.paths, environment.exec_env_flags, environment.opencode_web_credential_flags)

            self.assertFalse(restart.required)
            self.assertIn("Restarting OpenCode web server so package/config repairs take effect", restart_messages[0])
            self.assertEqual(plugin_messages, ())
            self.assertIn("opencode serve --hostname", plan.script)
            self.assertIn(OPENCODE_WEB_LOG_FILE, plan.argv)

    def test_stop_host_web_proxy_removes_cached_files(self) -> None:
        with runtime_workspace() as fixture:
            _ = fixture.paths.state.root.mkdir()
            _ = fixture.paths.state.host_proxy_pid_file.write_text("999999\n", encoding="utf-8")
            _ = fixture.paths.state.host_proxy_port_file.write_text("40000\n", encoding="utf-8")

            stop_host_web_proxy(fixture.paths)

            self.assertFalse(fixture.paths.state.host_proxy_pid_file.exists())
            self.assertFalse(fixture.paths.state.host_proxy_port_file.exists())


if __name__ == "__main__":
    _ = unittest.main()
