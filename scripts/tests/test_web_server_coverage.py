from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from typing import Final

from runtime_support import FakeResponse, RecordingEngine, runtime_workspace

SCRIPTS_DIR: Final = Path(__file__).resolve().parents[1]

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from overlord_py.env_builder import build_environment_plan  # noqa: E402
from overlord_py.runtime_config import RestartState  # noqa: E402
from overlord_py.web_serve_script import ENSURE_OPENCODE_WEB_SERVER_SCRIPT  # noqa: E402
from overlord_py.web_server import (  # noqa: E402
    plan_opencode_web_server,
    request_opencode_web_restart_if_plugin_env_missing,
    resolve_access_port_for_engine,
)


class WebManagerCoverageTests(unittest.TestCase):
    def test_composed_access_port_skips_host_proxy_for_docker_and_starts_it_for_podman(self) -> None:
        with runtime_workspace() as fixture:
            docker_port = resolve_access_port_for_engine("docker", fixture.paths, host_port="49152", env=fixture.runner_env, wait_seconds=0)

            self.assertEqual(docker_port, "49152")
            self.assertFalse(fixture.paths.state.host_proxy_script.exists())
            self.assertFalse(fixture.paths.state.host_proxy_pid_file.exists())

        with runtime_workspace() as fixture:
            fixture.workspace.install_fake_command("node")
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

    def test_opencode_serve_script_represents_reuse_stale_pure_start_pid_and_mode_marker_semantics(self) -> None:
        with runtime_workspace() as fixture:
            plan = plan_opencode_web_server(fixture.paths, (), (), "plain")
        script = plan.script

        assert_in_order(
            self,
            script,
            [
                'if [ -s "${PID_FILE}" ]; then',
                'kill -0 "${PID}"',
                '*"opencode serve --hostname ${HOST} --port ${PORT}"*|',
                'printf \'%s\\n\' "${DESIRED_MODE}" >"${MODE_FILE}"',
                'exit 0',
                '*"opencode web --pure --hostname ${HOST} --port ${PORT}"*|',
                'kill "${PID}" 2>/dev/null || true',
                'rm -f "${PID_FILE}"',
                'mkdir -p "$(dirname "${PID_FILE}")"',
                ': >"${LOG_FILE}"',
                'nohup opencode serve --hostname "${HOST}" --port "${PORT}" >"${LOG_FILE}" 2>&1 &',
                'echo $! >"${PID_FILE}"',
                'mkdir -p "$(dirname "${MODE_FILE}")"',
                'printf \'%s\\n\' "${DESIRED_MODE}" >"${MODE_FILE}"',
            ],
        )
        self.assertIs(script, ENSURE_OPENCODE_WEB_SERVER_SCRIPT)


def assert_in_order(test_case: unittest.TestCase, text: str, fragments: list[str]) -> None:
    cursor = 0
    for fragment in fragments:
        index = text.find(fragment, cursor)
        test_case.assertNotEqual(index, -1, fragment)
        cursor = index + len(fragment)


if __name__ == "__main__":
    unittest.main()
