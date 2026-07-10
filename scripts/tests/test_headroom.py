from __future__ import annotations

import sys
import unittest
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Final, Iterator

from harness import HarnessRun, TempLauncherWorkspace
from test_cli_characterization import headroom_unsupported_stderr


SCRIPTS_DIR: Final = Path(__file__).resolve().parents[1]
PYTHONPATH_ENV: Final = {"PYTHONPATH": str(SCRIPTS_DIR)}
PYTHON_ENTRYPOINT: Final = (sys.executable, "-m", "overlord_py.main")

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from overlord_py.engine import ContainerEngine  # noqa: E402
from overlord_py.env_builder import package_environment  # noqa: E402
from overlord_py.headroom import (  # noqa: E402
    HEADROOM_HEALTH_URL,
    HEADROOM_MODE_HEADROOM,
    HEADROOM_MODE_PLAIN,
    HEADROOM_PROXY_ARGS,
    HEADROOM_PROXY_COMMAND_DISPLAY,
    HEADROOM_PROXY_LOG_FILE,
    HEADROOM_PROXY_PID_FILE,
    HEADROOM_RUNTIME_ENV,
    desired_headroom_mode,
    is_valid_headroom_mode,
    plan_ensure_headroom_proxy,
    plan_headroom_runtime_availability_check,
    plan_stop_headroom_proxy,
    plan_wait_for_headroom_proxy,
    selected_headroom_route_status,
)
from overlord_py.paths import build_workspace_paths  # noqa: E402


CHECKED_IN_PRESETS: Final = (
    (
        "default",
        "oh-my-openagent.jsonc",
        "--config default (oh-my-openagent.jsonc): azure/gpt-5.6-sol is unsupported/unverified for Headroom mode.",
    ),
)


class HeadroomGuardTests(unittest.TestCase):
    def test_checked_in_routes_and_dynamic_lms_fail_before_startup(self) -> None:
        for preset, _filename, selected_status in CHECKED_IN_PRESETS:
            with self.subTest(preset=preset), launcher_workspace() as workspace:
                result = run_python(workspace, ("--headroom", "--config", preset, "web"), env={})

                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, "")
                self.assertEqual(result.stderr, headroom_unsupported_stderr(selected_status))
                self.assert_no_startup_invocations(workspace)

        with launcher_workspace() as workspace:
            result = run_python(workspace, ("--headroom", "--lms-model", "qwen", "web"), env={})

            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, "LM Studio override: all oh-my-openagent agents → lmstudio/qwen\n")
            self.assertEqual(
                result.stderr,
                headroom_unsupported_stderr("--lms-model qwen: dynamic lmstudio/qwen runtime override is unsupported/unverified for Headroom mode."),
            )
            self.assert_no_startup_invocations(workspace)

    def test_overlord_headroom_env_fails_before_startup(self) -> None:
        with launcher_workspace() as workspace:
            result = run_python(workspace, (), env={"OVERLORD_HEADROOM": "1"})

            self.assertEqual(result.returncode, 1)
            self.assertEqual(
                result.stderr,
                headroom_unsupported_stderr(
                    "--config default (oh-my-openagent.jsonc): azure/gpt-5.6-sol is unsupported/unverified for Headroom mode."
                ),
            )
            self.assert_no_startup_invocations(workspace)

    def assert_no_startup_invocations(self, workspace: TempLauncherWorkspace) -> None:
        for record in workspace.read_command_log():
            argv = record["argv"]
            self.assertNotIn("headroom", argv)
            if len(argv) > 1:
                self.assertNotIn(argv[1], {"build", "run", "exec", "port"})


class HeadroomPlanningTests(unittest.TestCase):
    def test_desired_mode_and_marker_values_accept_only_plain_and_headroom(self) -> None:
        self.assertEqual(HEADROOM_MODE_PLAIN, "plain")
        self.assertEqual(HEADROOM_MODE_HEADROOM, "headroom")
        self.assertEqual(desired_headroom_mode(False), HEADROOM_MODE_PLAIN)
        self.assertEqual(desired_headroom_mode(True), HEADROOM_MODE_HEADROOM)
        self.assertTrue(is_valid_headroom_mode(HEADROOM_MODE_PLAIN))
        self.assertTrue(is_valid_headroom_mode(HEADROOM_MODE_HEADROOM))
        self.assertFalse(is_valid_headroom_mode("stale"))

    def test_selected_route_status_preserves_all_current_unsupported_messages(self) -> None:
        for config_name, filename, selected_status in CHECKED_IN_PRESETS:
            with self.subTest(config_name=config_name):
                self.assertEqual(selected_headroom_route_status(config_name, filename, ""), selected_status)

    def test_proxy_command_plan_is_private_telemetry_off_and_deduplicates(self) -> None:
        with planning_workspace() as fixture:
            plan = plan_ensure_headroom_proxy(fixture.engine, fixture.paths, fixture.package_env)

            self.assertIn("exec", plan.argv)
            self.assertIn("-u", plan.argv)
            self.assertIn("overlord", plan.argv)
            self.assertIn("HEADROOM_TELEMETRY=off", plan.argv)
            self.assertEqual(HEADROOM_RUNTIME_ENV, ("HEADROOM_TELEMETRY=off",))
            self.assertEqual(HEADROOM_PROXY_ARGS, ("proxy", "--no-telemetry", "--host", "127.0.0.1", "--port", "8787"))
            self.assertEqual(
                HEADROOM_PROXY_COMMAND_DISPLAY,
                "HEADROOM_TELEMETRY=off headroom proxy --no-telemetry --host 127.0.0.1 --port 8787",
            )
            self.assertIn("headroom proxy --no-telemetry --host 127.0.0.1 --port 8787", " ".join(plan.argv))
            self.assertEqual(plan.script.count('printf \'%s\\n\' "$1" >"${pid_file}"'), 1)
            self.assertEqual(plan.script.count('write_pid_file "${selected_pid}"'), 1)
            self.assertEqual(plan.script.count('write_pid_file "$!"'), 1)
            self.assertIn("stop_pid", plan.script)
            self.assertIn("is_expected_headroom_proxy_cmdline", plan.script)
            self.assertIn("HEADROOM_TELEMETRY=${telemetry_value}", plan.script)
            self.assertIn(HEADROOM_PROXY_PID_FILE, plan.argv)
            self.assertIn(HEADROOM_PROXY_LOG_FILE, plan.argv)

    def test_runtime_wait_and_stop_plans_match_bash_contract(self) -> None:
        with planning_workspace() as fixture:
            package_env = fixture.package_env
            runtime = plan_headroom_runtime_availability_check(fixture.engine, fixture.paths, package_env)
            wait = plan_wait_for_headroom_proxy(fixture.engine, fixture.paths, package_env)
            stop = plan_stop_headroom_proxy(fixture.engine, fixture.paths, package_env)

            self.assertIn("0.27.0", runtime.argv)
            self.assertIn("headroom --version", runtime.script)
            self.assertIn("headroom proxy --help", runtime.script)
            self.assertIn(HEADROOM_HEALTH_URL, wait.argv)
            self.assertIn("curl -fsS", wait.script)
            self.assertIn(HEADROOM_PROXY_PID_FILE, stop.argv)
            self.assertIn("kill -9", stop.script)

    def test_container_run_args_do_not_publish_headroom_port(self) -> None:
        with planning_workspace() as fixture:
            from overlord_py.container_lifecycle import build_container_run_args

            args = build_container_run_args(fixture.paths, (), home=fixture.home)

            self.assertIn("0.0.0.0::4090", args)
            self.assertFalse(any("8787" in arg for arg in args))


class HeadroomManualQaTests(unittest.TestCase):
    def test_manual_qa_surface_exercises_fail_fast_and_private_proxy_plan(self) -> None:
        with launcher_workspace() as workspace:
            fail_fast = run_python(workspace, ("--headroom", "--config", "default"), env={})
            self.assertEqual(fail_fast.returncode, 1)
            self.assertEqual(workspace.read_command_log(), [])

        with planning_workspace() as fixture:
            plan = plan_ensure_headroom_proxy(fixture.engine, fixture.paths, fixture.package_env)
            self.assertIn("--host", plan.argv)
            self.assertIn("127.0.0.1", plan.argv)
            self.assertIn("--port", plan.argv)
            self.assertIn("8787", plan.argv)
            self.assertIn("--no-telemetry", plan.argv)


class PlanningFixture:
    def __init__(self, workspace: TempLauncherWorkspace, home: Path) -> None:
        self.workspace = workspace
        self.home = home
        self.paths = build_workspace_paths(workspace.path, script_path=SCRIPTS_DIR / "overlord")
        self.engine = ContainerEngine("docker")
        self.package_env = package_environment()


@contextmanager
def launcher_workspace() -> Iterator[TempLauncherWorkspace]:
    with TempLauncherWorkspace() as workspace:
        workspace.install_fake_engine("podman", state="missing", image_exists=False)
        yield workspace


@contextmanager
def planning_workspace() -> Iterator[PlanningFixture]:
    with TempLauncherWorkspace(workspace_name="Headroom Project") as workspace:
        home = workspace.path / "host-home"
        home.mkdir()
        yield PlanningFixture(workspace, home)


def run_python(workspace: TempLauncherWorkspace, args: tuple[str, ...], *, env: Mapping[str, str]) -> HarnessRun:
    merged_env = dict(PYTHONPATH_ENV)
    merged_env.update(env)
    return workspace.run_command((*PYTHON_ENTRYPOINT, *args), env=merged_env)


if __name__ == "__main__":
    unittest.main()
