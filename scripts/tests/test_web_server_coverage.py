from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from typing import Final

TESTS_DIR: Final = Path(__file__).resolve().parent
SCRIPTS_DIR: Final = TESTS_DIR.parent

for import_path in (TESTS_DIR, SCRIPTS_DIR):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

from scripts.overlord_py.env_builder import build_environment_plan
from scripts.overlord_py.opencode_cmdline_matcher import OPENCODE_CMDLINE_MATCHER_SCRIPT
from scripts.overlord_py.runtime_config import RestartState
from scripts.overlord_py.web_serve_script import ENSURE_OPENCODE_WEB_SERVER_SCRIPT
from scripts.overlord_py.web_server import (
    plan_opencode_web_server,
    request_opencode_web_restart_if_plugin_env_missing,
    resolve_access_port_for_engine,
)
from scripts.tests.runtime_support import FakeResponse, RecordingEngine, runtime_workspace


class WebManagerCoverageTests(unittest.TestCase):
    def test_composed_access_port_skips_host_proxy_for_docker_and_starts_it_for_podman(self) -> None:
        with runtime_workspace() as fixture:
            docker_port = resolve_access_port_for_engine("docker", fixture.paths, host_port="49152", env=fixture.runner_env, wait_seconds=0)

            self.assertEqual(docker_port, "49152")
            self.assertFalse(fixture.paths.state.host_proxy_script.exists())
            self.assertFalse(fixture.paths.state.host_proxy_pid_file.exists())

        with runtime_workspace() as fixture:
            _ = fixture.workspace.install_fake_command("node")
            env = {"PATH": f"{fixture.workspace.fake_bin}{os.pathsep}{os.environ.get('PATH', '')}"}

            podman_port = resolve_access_port_for_engine("podman", fixture.paths, host_port="49152", env=env, wait_seconds=0)

            self.assertEqual(podman_port, "49152")
            self.assertTrue(fixture.paths.state.host_proxy_script.exists())
            self.assertTrue(fixture.paths.state.host_proxy_pid_file.exists())
            self.assertIn('server.listen(0, bindHost', fixture.paths.state.host_proxy_script.read_text(encoding="utf-8"))
            self.assertTrue(fixture.paths.state.host_proxy_pid_file.read_text(encoding="utf-8").strip().isdigit())

    def test_plugin_env_mismatch_requests_restart_when_restart_is_not_already_required(self) -> None:
        engine = RecordingEngine(responses=[("process_has_env_value", FakeResponse(returncode=1))])
        with runtime_workspace(engine=engine) as fixture:
            restart = RestartState()
            environment = build_environment_plan(
                {"HOME": str(fixture.workspace.path / "host-home"), "EXA_API_KEY": "sentinel-exa"},
                home=fixture.workspace.path / "host-home",
                workspace_name=fixture.paths.identity.workspace_name,
            )

            messages = request_opencode_web_restart_if_plugin_env_missing(
                engine,
                fixture.paths,
                restart,
                env=fixture.runner_env,
                credential_flags=environment.opencode_web_credential_flags,
            )

            self.assertTrue(restart.required)
            self.assertEqual(messages, (f"Restarting existing OpenCode web server because its HOME/XDG/CodeGraph/MCP credential environment is not canonical in {fixture.paths.identity.container_name}...",))
            self.assertTrue(any("OVERLORD_HOST_EXA_API_KEY_PRESENT=1" in run.args for run in engine.runs))
            self.assertTrue(any("process_has_env_value EXA_API_KEY" in (run.input_text or "") for run in engine.runs))

    def test_opencode_serve_plan_composes_shared_pid_classifier(self) -> None:
        with runtime_workspace() as fixture:
            plan = plan_opencode_web_server(fixture.paths, (), ())

        self.assertEqual(plan.script, ENSURE_OPENCODE_WEB_SERVER_SCRIPT)
        self.assertTrue(plan.script.startswith(OPENCODE_CMDLINE_MATCHER_SCRIPT))


if __name__ == "__main__":
    _ = unittest.main()
