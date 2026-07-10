from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Final

TESTS_DIR: Final = Path(__file__).resolve().parent
SCRIPTS_DIR: Final = TESTS_DIR.parent
REPO_ROOT: Final = SCRIPTS_DIR.parent

for import_path in (REPO_ROOT, TESTS_DIR, SCRIPTS_DIR):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

from scripts.tests.runtime_support import FakeResponse, RecordingEngine, runtime_workspace  # noqa: E402
from scripts.overlord_py import web_restart  # noqa: E402
from scripts.overlord_py.headroom import HEADROOM_MODE_FILE  # noqa: E402
from scripts.overlord_py.runtime_config import RestartState  # noqa: E402
from scripts.overlord_py.web_restart import workspace_project_stale_check_args  # noqa: E402
from scripts.overlord_py.web_types import OPENCODE_WEB_HOSTNAME, OPENCODE_WEB_PID_FILE, OPENCODE_WEB_PORT, OPENCODE_WEB_WAIT_SECONDS  # noqa: E402


class WebRestartTests(unittest.TestCase):
    def test_restart_forwards_pid_mode_hostname_and_port_to_script(self) -> None:
        engine = RecordingEngine()
        with runtime_workspace(engine=engine) as fixture:
            restart = RestartState(required=True)

            _ = web_restart.restart_opencode_web_if_needed(
                engine,
                fixture.paths,
                restart,
                env=fixture.runner_env,
            )

            script_args = engine.runs[0].args
            self.assertEqual(
                script_args[script_args.index("--") + 1 :],
                [OPENCODE_WEB_PID_FILE, HEADROOM_MODE_FILE, OPENCODE_WEB_HOSTNAME, OPENCODE_WEB_PORT],
            )

    def test_plugin_env_mismatch_requests_shared_restart(self) -> None:
        engine = RecordingEngine(responses=[("process_has_env_value", FakeResponse(returncode=1))])
        with runtime_workspace(engine=engine) as fixture:
            restart = RestartState()

            messages = web_restart.request_opencode_web_restart_if_plugin_env_missing(
                engine,
                fixture.paths,
                restart,
                env=fixture.runner_env,
                credential_flags=("-e", "OPENCODE_SERVER_PASSWORD=mismatch-secret"),
            )

            self.assertTrue(restart.required)
            self.assertEqual(len(messages), 1)
            probe_run = engine.runs[0]
            self.assertNotIn("mismatch-secret", probe_run.args[probe_run.args.index("--") + 1 :])
            self.assertNotIn("mismatch-secret", "".join(messages))

    def test_plugin_env_match_does_not_request_restart(self) -> None:
        engine = RecordingEngine()
        with runtime_workspace(engine=engine) as fixture:
            restart = RestartState()

            messages = web_restart.request_opencode_web_restart_if_plugin_env_missing(
                engine,
                fixture.paths,
                restart,
                env=fixture.runner_env,
                credential_flags=("-e", "OPENCODE_SERVER_PASSWORD=matching-secret"),
            )

            self.assertFalse(restart.required)
            self.assertEqual(messages, ())
            self.assertEqual(len(engine.runs), 1)
            probe_run = engine.runs[0]
            script_args = probe_run.args[probe_run.args.index("--") + 1 :]
            self.assertEqual(script_args[0:3], [OPENCODE_WEB_PID_FILE, OPENCODE_WEB_HOSTNAME, OPENCODE_WEB_PORT])
            self.assertNotIn("matching-secret", script_args)

    def test_plugin_env_probe_is_skipped_when_restart_is_already_pending(self) -> None:
        engine = RecordingEngine()
        with runtime_workspace(engine=engine) as fixture:
            restart = RestartState(required=True)

            messages = web_restart.request_opencode_web_restart_if_plugin_env_missing(
                engine,
                fixture.paths,
                restart,
                env=fixture.runner_env,
                credential_flags=("-e", "OPENCODE_SERVER_PASSWORD=pending-secret"),
            )

            self.assertTrue(restart.required)
            self.assertEqual(messages, ())
            self.assertEqual(engine.runs, [])

    def test_workspace_project_probe_error_raises_when_probe_returns_two(self) -> None:
        engine = RecordingEngine(
            responses=[("workspace_project_is_stale", FakeResponse(returncode=2, stderr="probe failed"))],
        )
        with runtime_workspace(engine=engine) as fixture:
            # Given: an auth, transport, HTTP, or response-shape probe failure.
            # When: the probe result is translated to Python.
            with self.assertRaisesRegex(web_restart.WebServerError, "probe failed"):
                web_restart.workspace_project_is_stale(
                    engine,
                    fixture.paths,
                    env=fixture.runner_env,
                    credential_flags=(),
                )

            # Then: the result cannot be mistaken for stale or healthy.

    def test_workspace_project_probe_error_raises_when_probe_returns_unknown_status(self) -> None:
        engine = RecordingEngine(
            responses=[("workspace_project_is_stale", FakeResponse(returncode=7))],
        )
        with runtime_workspace(engine=engine) as fixture:
            # Given: an exit status outside the shell probe protocol.
            # When: the probe result is translated to Python.
            with self.assertRaisesRegex(web_restart.WebServerError, "exit code 7"):
                web_restart.workspace_project_is_stale(
                    engine,
                    fixture.paths,
                    env=fixture.runner_env,
                    credential_flags=(),
                )

            # Then: unknown statuses fail closed.

    def test_workspace_project_is_stale_when_probe_returns_one(self) -> None:
        engine = RecordingEngine(
            responses=[("workspace_project_is_stale", FakeResponse(returncode=1))],
        )
        with runtime_workspace(engine=engine) as fixture:
            # Given: a probe that confirms the global project cache.
            credential_flags: tuple[str, ...] = ()

            # When: the probe result is translated to Python.
            is_stale = web_restart.workspace_project_is_stale(
                engine,
                fixture.paths,
                env=fixture.runner_env,
                credential_flags=credential_flags,
            )

            # Then: exit one is the sole stale result.
            self.assertTrue(is_stale)

    def test_workspace_project_is_not_stale_when_probe_returns_zero(self) -> None:
        engine = RecordingEngine(
            responses=[("workspace_project_is_stale", FakeResponse(returncode=0))],
        )
        with runtime_workspace(engine=engine) as fixture:
            # Given: a successful workspace-project probe.
            credential_flags = ("-e", "OPENCODE_SERVER_PASSWORD=secret")

            # When: the probe result is translated to Python.
            is_stale = web_restart.workspace_project_is_stale(
                engine,
                fixture.paths,
                env=fixture.runner_env,
                credential_flags=credential_flags,
            )

            # Then: exit zero means healthy or not applicable.
            self.assertFalse(is_stale)

    def test_workspace_project_stale_check_places_credential_flags_before_container_name(self) -> None:
        engine = RecordingEngine()
        with runtime_workspace(engine=engine) as fixture:
            # Given: credential flags containing the web-server password.
            credential_flags = ("-e", "OPENCODE_SERVER_PASSWORD=secret")

            # When: the stale-check argv is planned.
            args = workspace_project_stale_check_args(fixture.paths, credential_flags)

            # Then: credentials are engine flags rather than positional script arguments.
            self.assertEqual(args[0:4], ["exec", "-i", "-e", "OPENCODE_SERVER_PASSWORD=secret"])
            self.assertEqual(args[4], fixture.paths.identity.container_name)
            script_args = args[args.index("--") + 1 :]
            self.assertEqual(script_args, [OPENCODE_WEB_PID_FILE, OPENCODE_WEB_HOSTNAME, OPENCODE_WEB_PORT, "/workspace", str(OPENCODE_WEB_WAIT_SECONDS)])
            self.assertNotIn("secret", script_args)

if __name__ == "__main__":
    unittest.main()
