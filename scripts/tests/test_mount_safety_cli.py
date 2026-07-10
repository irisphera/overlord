from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path
import socket
import sys
from typing import Final
import unittest
from unittest.mock import patch

from harness import HarnessRun, TempLauncherWorkspace, valid_persisted_state_inspect


SCRIPTS_DIR: Final = Path(__file__).resolve().parents[1]
PYTHON_ENTRYPOINT: Final = (sys.executable, "-m", "overlord_py.main")
EXPECTED_SAFETY_ERROR: Final = (
    "Error: mount-safety check failed; the destructive operation was refused. "
    "Persisted state was not changed. Resolve the reported mount problem or follow the README legacy-container "
    "migration steps before retrying.\n"
    "Details: Expected exactly one mount at /workspace; found 0\n"
)

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from overlord_py import main  # noqa: E402
from overlord_py.cli import CliOptions, Command  # noqa: E402
from overlord_py.docker_bind_sources import MOUNT_FORMAT  # noqa: E402
from overlord_py.engine import ContainerEngine  # noqa: E402
from overlord_py.paths import WorkspacePaths, build_workspace_paths  # noqa: E402
from overlord_py.persisted_state_mounts import MountSafetyFailure  # noqa: E402


class MountSafetyCliTests(unittest.TestCase):
    def test_failed_cli_mount_inspection_refuses_without_traceback_or_side_effects(self) -> None:
        for command in (Command.FRESH, Command.PURGE):
            with self.subTest(command=command), TempLauncherWorkspace() as workspace:
                # Given
                workspace.install_fake_engine(
                    "docker",
                    state="running",
                    image_exists=True,
                    raw_inspect_output='[{"Mounts":[]}]',
                )
                paths = launcher_paths(workspace)
                paths.state.root.mkdir()
                paths.state.host_proxy_pid_file.write_text("not-a-pid\n", encoding="utf-8")
                paths.state.host_proxy_port_file.write_text("49152\n", encoding="utf-8")

                # When
                result = run_cli(workspace, command)

                # Then
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stderr, EXPECTED_SAFETY_ERROR)
                self.assertNotIn("Traceback", result.stderr)
                self.assertTrue(paths.state.host_proxy_pid_file.exists())
                self.assertTrue(paths.state.host_proxy_port_file.exists())
                self.assertEqual(engine_argv(workspace), preflight_argv(paths))

    def test_failed_run_launcher_mount_inspection_does_not_stop_host_proxy(self) -> None:
        for command in (Command.FRESH, Command.PURGE):
            with self.subTest(command=command), TempLauncherWorkspace() as workspace:
                # Given
                workspace.install_fake_engine(
                    "docker",
                    state="running",
                    image_exists=True,
                    raw_inspect_output='[{"Mounts":[]}]',
                )
                paths = launcher_paths(workspace)
                engine = ContainerEngine("docker")

                # When / Then
                with patch.object(main, "stop_host_web_proxy") as stop_proxy, self.assertRaises(MountSafetyFailure):
                    _ = main.run_launcher(engine, paths, options(command), runner_env(workspace))
                stop_proxy.assert_not_called()
                self.assertEqual(engine_argv(workspace), preflight_argv(paths))

    def test_valid_mounts_stop_host_proxy_after_inspect_before_destruction(self) -> None:
        for command in (Command.FRESH, Command.PURGE):
            with self.subTest(command=command), TempLauncherWorkspace() as workspace:
                # Given
                workspace.install_fake_engine(
                    "docker",
                    state="running",
                    image_exists=True,
                    raw_inspect_output=valid_persisted_state_inspect(workspace.path),
                )
                paths = launcher_paths(workspace)
                engine = ContainerEngine("docker")
                argv_at_proxy_stop: list[list[str]] = []

                def record_proxy_stop(_paths: WorkspacePaths) -> None:
                    argv_at_proxy_stop.extend(engine_argv(workspace))

                # When
                with patch.object(main, "stop_host_web_proxy", side_effect=record_proxy_stop) as stop_proxy, contextlib.redirect_stdout(io.StringIO()):
                    status = main.run_launcher(engine, paths, options(command), runner_env(workspace))

                # Then
                self.assertEqual(status, 0)
                stop_proxy.assert_called_once_with(paths)
                self.assertEqual(argv_at_proxy_stop, preflight_argv(paths))
                commands = engine_argv(workspace)
                self.assertEqual(commands[:2], preflight_argv(paths))
                self.assertEqual(commands[2][1:3], ["rm", "-f"] if command is Command.PURGE else ["stop", paths.identity.container_name])


def options(command: Command) -> CliOptions:
    return CliOptions(
        command=command,
        config_name="default",
        config_file=Path("oh-my-openagent.jsonc"),
        config_explicit=False,
        lms_model="",
        model_override="",
        headroom_enabled=False,
        desired_headroom_mode="off",
    )


def launcher_paths(workspace: TempLauncherWorkspace) -> WorkspacePaths:
    return build_workspace_paths(workspace.path, script_path=SCRIPTS_DIR / "overlord")


def runner_env(workspace: TempLauncherWorkspace) -> dict[str, str]:
    return {
        "PATH": f"{workspace.fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
        "FAKE_COMMAND_LOG": str(workspace.log_path),
    }


def run_cli(workspace: TempLauncherWorkspace, command: Command) -> HarnessRun:
    env = {"PYTHONPATH": str(SCRIPTS_DIR), "HOME": str(workspace.path / "host-home")}
    return workspace.run_command((*PYTHON_ENTRYPOINT, command.value), env=env)


def engine_argv(workspace: TempLauncherWorkspace) -> list[list[str]]:
    return [record["argv"] for record in workspace.read_command_log() if record["executable"] == "docker"]


def preflight_argv(paths: WorkspacePaths) -> list[list[str]]:
    return [
        ["docker", "inspect", "--format", MOUNT_FORMAT, socket.gethostname()],
        ["docker", "inspect", paths.identity.container_name],
    ]
