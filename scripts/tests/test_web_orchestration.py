from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
import sys
import unittest
from pathlib import Path
from typing import Final
from unittest.mock import Mock, patch

from runtime_support import FakeResponse, RecordingEngine, runtime_workspace


SCRIPTS_DIR: Final = Path(__file__).resolve().parents[1]

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from overlord_py import main  # noqa: E402
from overlord_py.cli import CliOptions, Command  # noqa: E402
from overlord_py.env_builder import EnvironmentPlan, build_environment_plan  # noqa: E402
from overlord_py.paths import WorkspacePaths  # noqa: E402
from overlord_py.runtime_config import RestartState  # noqa: E402
from overlord_py.web_scripts import ENSURE_OPENCODE_WEB_SERVER_SCRIPT, REQUEST_RESTART_IF_WORKSPACE_PROJECT_STALE_SCRIPT, RESTART_OPENCODE_WEB_SCRIPT  # noqa: E402


@dataclass(frozen=True, slots=True)
class OrchestrationFixture:
    engine: RecordingEngine
    paths: WorkspacePaths
    environment: EnvironmentPlan
    options: CliOptions
    restart: RestartState
    env: Mapping[str, str]
    health: Mock
    ui: Mock
    access: Mock
    mcp: Mock
    network: Mock
    readiness_scripts: list[tuple[str, ...]]

    def run(self) -> None:
        main.ensure_web_server(self.engine, self.paths, self.environment, self.options, self.restart, env=self.env)

    def count_script(self, script: str) -> int:
        return sum(run.input_text == script for run in self.engine.runs)


class WebOrchestrationTests(unittest.TestCase):
    def test_pending_restart_runs_once_before_first_readiness_attempt(self) -> None:
        with orchestration_fixture((0,), restart_required=True) as fixture:
            # Given: password reconciliation already requested a web restart.
            # When: the healthy web orchestration runs.
            fixture.run()

            # Then: restart precedes ensure and both readiness checks on the only attempt.
            self.assertEqual(
                fixture.readiness_scripts,
                [
                    (RESTART_OPENCODE_WEB_SCRIPT, ENSURE_OPENCODE_WEB_SERVER_SCRIPT),
                    (RESTART_OPENCODE_WEB_SCRIPT, ENSURE_OPENCODE_WEB_SERVER_SCRIPT),
                ],
            )
            self.assertEqual(fixture.count_script(RESTART_OPENCODE_WEB_SCRIPT), 1)
            self.assertEqual(fixture.count_script(ENSURE_OPENCODE_WEB_SERVER_SCRIPT), 1)
            self.assertEqual(fixture.count_script(REQUEST_RESTART_IF_WORKSPACE_PROJECT_STALE_SCRIPT), 1)
            self.assertEqual(fixture.health.call_count, 1)
            self.assertEqual(fixture.ui.call_count, 1)

    def test_pending_restart_failure_preserves_state_and_stops_downstream_work(self) -> None:
        with orchestration_fixture(
            (),
            restart_required=True,
            restart_response=FakeResponse(returncode=1, stderr="password restart failed"),
        ) as fixture:
            # Given: password reconciliation requested a restart that will fail.
            # When: web orchestration attempts the pending restart.
            with self.assertRaisesRegex(main.WebServerError, "password restart failed"):
                fixture.run()

            # Then: pending state remains and no readiness or publication work starts.
            self.assertTrue(fixture.restart.required)
            self.assertEqual(fixture.count_script(RESTART_OPENCODE_WEB_SCRIPT), 1)
            self.assertEqual(fixture.count_script(ENSURE_OPENCODE_WEB_SERVER_SCRIPT), 0)
            self.assertEqual(fixture.count_script(REQUEST_RESTART_IF_WORKSPACE_PROJECT_STALE_SCRIPT), 0)
            fixture.health.assert_not_called()
            fixture.ui.assert_not_called()
            fixture.access.assert_not_called()
            fixture.mcp.assert_not_called()
            fixture.network.assert_not_called()

    def test_healthy_first_attempt_runs_downstream_work_once(self) -> None:
        with orchestration_fixture((0,)) as fixture:
            # Given: the first credentialed project probe is healthy.
            # When: the web server is ensured.
            fixture.run()

            # Then: one attempt completes all downstream work.
            self.assertEqual(fixture.count_script(ENSURE_OPENCODE_WEB_SERVER_SCRIPT), 1)
            self.assertEqual(fixture.count_script(REQUEST_RESTART_IF_WORKSPACE_PROJECT_STALE_SCRIPT), 1)
            self.assertEqual(fixture.count_script(RESTART_OPENCODE_WEB_SCRIPT), 0)
            self.assertEqual(fixture.health.call_count, 1)
            self.assertEqual(fixture.ui.call_count, 1)
            self.assertEqual(fixture.access.call_count, 1)
            self.assertEqual(fixture.mcp.call_count, 1)

    def test_stale_first_attempt_restarts_once_then_repeats_readiness(self) -> None:
        with orchestration_fixture((1, 0)) as fixture:
            # Given: the first probe is stale and the second is healthy.
            # When: the bounded web orchestration runs.
            fixture.run()

            # Then: one restart separates two complete readiness attempts.
            self.assertEqual(fixture.count_script(ENSURE_OPENCODE_WEB_SERVER_SCRIPT), 2)
            self.assertEqual(fixture.count_script(REQUEST_RESTART_IF_WORKSPACE_PROJECT_STALE_SCRIPT), 2)
            self.assertEqual(fixture.count_script(RESTART_OPENCODE_WEB_SCRIPT), 1)
            self.assertEqual(fixture.health.call_count, 2)
            self.assertEqual(fixture.ui.call_count, 2)
            self.assertEqual(fixture.access.call_count, 1)
            self.assertEqual(fixture.mcp.call_count, 1)

    def test_stale_twice_fails_after_one_restart_without_downstream_work(self) -> None:
        with orchestration_fixture((1, 1)) as fixture:
            # Given: both bounded project probes confirm stale global state.
            # When: the second attempt remains stale.
            with self.assertRaisesRegex(main.WebServerError, "workspace project cache remained stale"):
                fixture.run()

            # Then: orchestration stops after one restart and skips downstream work.
            self.assertEqual(fixture.count_script(ENSURE_OPENCODE_WEB_SERVER_SCRIPT), 2)
            self.assertEqual(fixture.count_script(REQUEST_RESTART_IF_WORKSPACE_PROJECT_STALE_SCRIPT), 2)
            self.assertEqual(fixture.count_script(RESTART_OPENCODE_WEB_SCRIPT), 1)
            self.assertEqual(fixture.health.call_count, 2)
            self.assertEqual(fixture.ui.call_count, 2)
            self.assertEqual(fixture.access.call_count, 0)
            self.assertEqual(fixture.mcp.call_count, 0)

    def test_probe_error_propagates_without_restart_or_downstream_work(self) -> None:
        with orchestration_fixture((2,)) as fixture:
            # Given: the credentialed project probe reports an auth or transport error.
            # When: the first post-start probe runs.
            with self.assertRaisesRegex(main.WebServerError, "project probe failed"):
                fixture.run()

            # Then: the error fails closed before restart or downstream work.
            self.assertEqual(fixture.count_script(ENSURE_OPENCODE_WEB_SERVER_SCRIPT), 1)
            self.assertEqual(fixture.count_script(REQUEST_RESTART_IF_WORKSPACE_PROJECT_STALE_SCRIPT), 1)
            self.assertEqual(fixture.count_script(RESTART_OPENCODE_WEB_SCRIPT), 0)
            self.assertEqual(fixture.access.call_count, 0)
            self.assertEqual(fixture.mcp.call_count, 0)


@contextmanager
def orchestration_fixture(
    probe_statuses: tuple[int, ...],
    *,
    restart_required: bool = False,
    restart_response: FakeResponse | None = None,
) -> Iterator[OrchestrationFixture]:
    engine = RecordingEngine()
    with runtime_workspace(engine=engine) as runtime, ExitStack() as stack:
        if restart_response is not None:
            engine.responses.append((RESTART_OPENCODE_WEB_SCRIPT, restart_response))
        for status in probe_statuses:
            engine.responses.extend(
                (
                    (f"port {runtime.paths.identity.container_name} 4090/tcp", FakeResponse(stdout="0.0.0.0:49152\n")),
                    ("workspace_project_is_stale", FakeResponse(returncode=status, stderr="project probe failed" if status == 2 else "")),
                )
            )
        environment = build_environment_plan(
            {"HOME": str(runtime.workspace.path / "host-home"), "OPENCODE_SERVER_PASSWORD": "secret"},
            home=runtime.workspace.path / "host-home",
            workspace_name=runtime.paths.identity.workspace_name,
        )
        options = CliOptions(
            command=Command.WEB,
            config_name="default",
            config_file=runtime.context.oh_my_config_file,
            config_explicit=False,
            lms_model="",
            model_override="",
            headroom_enabled=False,
            desired_headroom_mode="plain",
        )
        readiness_scripts: list[tuple[str, ...]] = []

        def record_readiness(_host_port: str, *, password: str) -> None:
            del _host_port, password
            readiness_scripts.append(
                tuple(
                    run.input_text
                    for run in engine.runs
                    if run.input_text in {RESTART_OPENCODE_WEB_SCRIPT, ENSURE_OPENCODE_WEB_SERVER_SCRIPT}
                )
            )

        stack.enter_context(patch("overlord_py.main.ensure_opencode_runtime_version", return_value=()))
        stack.enter_context(patch("overlord_py.main.stdout_stage"))
        health = stack.enter_context(patch("overlord_py.main.wait_for_opencode_web", side_effect=record_readiness))
        ui = stack.enter_context(patch("overlord_py.main.wait_for_opencode_web_ui", side_effect=record_readiness))
        access = stack.enter_context(patch("overlord_py.main.resolve_access_port_for_engine", return_value="49152"))
        mcp = stack.enter_context(patch("overlord_py.main.verify_oh_my_openagent_loaded"))
        network = stack.enter_context(patch("overlord_py.main.resolve_network_host_ip", return_value=""))
        stack.enter_context(patch("overlord_py.main.format_access_urls", return_value=""))
        yield OrchestrationFixture(
            engine,
            runtime.paths,
            environment,
            options,
            RestartState(required=restart_required),
            runtime.runner_env,
            health,
            ui,
            access,
            mcp,
            network,
            readiness_scripts,
        )


if __name__ == "__main__":
    unittest.main()
