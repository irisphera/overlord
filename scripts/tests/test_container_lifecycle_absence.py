from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
from pathlib import Path
import socket
import sys
from typing import Final, override
import unittest

from harness import TempLauncherWorkspace


SCRIPTS_DIR: Final = Path(__file__).resolve().parents[1]

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from overlord_py.container_lifecycle import LifecycleError, local_image_ref, purge  # noqa: E402
from overlord_py.docker_bind_sources import MOUNT_FORMAT  # noqa: E402
from overlord_py.engine import CommandResult, ContainerEngine  # noqa: E402
from overlord_py.paths import WorkspacePaths, build_workspace_paths  # noqa: E402
from overlord_py.persisted_state_mounts import MountSafetyFailure  # noqa: E402


type EngineResponse = tuple[int, str, str]
type EngineResponses = Mapping[tuple[str, ...], EngineResponse]


class ContainerLifecycleAbsenceTests(unittest.TestCase):
    def test_docker_absence_runs_callback_and_image_cleanup_without_mount_inspection(self) -> None:
        with TempLauncherWorkspace(workspace_name="Docker Absent") as workspace:
            # Given
            paths = launcher_paths(workspace)
            state_query = docker_state_query(paths)
            query = docker_presence_query(paths)
            engine = ScriptedEngine("docker", {state_query: (1, "", "not found"), query: (0, "", "")})
            callback_runs: list[tuple[tuple[str, ...], ...]] = []

            # When
            messages = purge(
                engine,
                paths,
                env={},
                after_verification=lambda: callback_runs.append(tuple(engine.runs)),
            )

            # Then
            self.assertEqual(callback_runs, [(state_query, query)])
            self.assertIn(f"==> Container {paths.identity.container_name} is already absent.", messages)
            self.assertEqual(engine.runs, [state_query, query, *image_cleanup_commands(paths)])

    def test_podman_absence_runs_callback_and_image_cleanup_without_mount_inspection(self) -> None:
        with TempLauncherWorkspace(workspace_name="Podman Absent") as workspace:
            # Given
            paths = launcher_paths(workspace)
            query = ("container", "exists", paths.identity.container_name)
            engine = ScriptedEngine("podman", {query: (1, "", "")})
            callback_runs: list[tuple[tuple[str, ...], ...]] = []

            # When
            messages = purge(
                engine,
                paths,
                env={},
                after_verification=lambda: callback_runs.append(tuple(engine.runs)),
            )

            # Then
            self.assertEqual(callback_runs, [(query,)])
            self.assertIn(f"==> Container {paths.identity.container_name} is already absent.", messages)
            self.assertEqual(engine.runs, [query, *image_cleanup_commands(paths)])

    def test_docker_presence_query_failure_after_missing_state_is_fail_closed(self) -> None:
        with TempLauncherWorkspace(workspace_name="Docker Query Failure") as workspace:
            # Given
            paths = launcher_paths(workspace)
            state_query = docker_state_query(paths)
            query = docker_presence_query(paths)
            engine = ScriptedEngine(
                "docker",
                {
                    state_query: (1, "", "inspect failed"),
                    query: (2, "", "daemon unavailable"),
                },
            )
            callback_called = False

            def record_callback() -> None:
                nonlocal callback_called
                callback_called = True

            # When / Then
            with self.assertRaisesRegex(LifecycleError, "failed to check container existence: daemon unavailable"):
                _ = purge(engine, paths, env={}, after_verification=record_callback)
            self.assertFalse(callback_called)
            self.assertEqual(engine.runs, [state_query, query])

    def test_podman_unexpected_presence_status_is_fail_closed(self) -> None:
        with TempLauncherWorkspace(workspace_name="Podman Query Failure") as workspace:
            # Given
            paths = launcher_paths(workspace)
            query = ("container", "exists", paths.identity.container_name)
            engine = ScriptedEngine("podman", {query: (125, "", "storage unavailable")})

            # When / Then
            with self.assertRaisesRegex(LifecycleError, "failed to check container existence: storage unavailable"):
                _ = purge(engine, paths, env={})
            self.assertEqual(engine.runs, [query])

    def test_docker_lookalike_name_does_not_count_as_present(self) -> None:
        with TempLauncherWorkspace(workspace_name="Docker Lookalike") as workspace:
            # Given
            paths = launcher_paths(workspace)
            state_query = docker_state_query(paths)
            query = docker_presence_query(paths)
            engine = ScriptedEngine(
                "docker",
                {
                    state_query: (1, "", "not found"),
                    query: (0, f"{paths.identity.container_name}-old\n", ""),
                },
            )

            # When
            messages = purge(engine, paths, env={})

            # Then
            self.assertIn(f"==> Container {paths.identity.container_name} is already absent.", messages)
            self.assertEqual(engine.runs, [state_query, query, *image_cleanup_commands(paths)])

    def test_docker_exact_name_fallback_counts_as_present(self) -> None:
        with TempLauncherWorkspace(workspace_name="Docker Exact Fallback") as workspace:
            # Given
            paths = launcher_paths(workspace)
            state_query = docker_state_query(paths)
            query = docker_presence_query(paths)
            target_inspect = ("inspect", paths.identity.container_name)
            engine = ScriptedEngine(
                "docker",
                {
                    state_query: (1, "", "not found"),
                    query: (0, f"{paths.identity.container_name}\n", ""),
                    target_inspect: (0, valid_mount_inspect(workspace.path), ""),
                },
            )

            # When
            messages = purge(engine, paths, env={})

            # Then
            self.assertNotIn(f"==> Container {paths.identity.container_name} is already absent.", messages)
            self.assertEqual(
                engine.runs,
                [
                    state_query,
                    query,
                    ("inspect", "--format", MOUNT_FORMAT, socket.gethostname()),
                    target_inspect,
                    ("rm", "-f", paths.identity.container_name),
                    *image_cleanup_commands(paths),
                ],
            )

    def test_present_container_mount_inspect_failure_is_fail_closed(self) -> None:
        with TempLauncherWorkspace(workspace_name="Present Inspect Failure") as workspace:
            # Given
            paths = launcher_paths(workspace)
            state_query = docker_state_query(paths)
            target_inspect = ("inspect", paths.identity.container_name)
            engine = ScriptedEngine(
                "docker",
                {
                    state_query: (0, "running\n", ""),
                    target_inspect: (1, "", "inspect failed"),
                },
            )

            # When / Then
            with self.assertRaisesRegex(MountSafetyFailure, "Cannot verify persisted-state mounts: inspect failed"):
                _ = purge(engine, paths, env={})
            self.assertEqual(
                engine.runs,
                [
                    state_query,
                    ("inspect", "--format", MOUNT_FORMAT, socket.gethostname()),
                    target_inspect,
                ],
            )


@dataclass(frozen=True, slots=True)
class ScriptedEngine(ContainerEngine):
    responses: EngineResponses
    runs: list[tuple[str, ...]]

    def __init__(self, name: str, responses: EngineResponses) -> None:
        ContainerEngine.__init__(self, name)
        object.__setattr__(self, "responses", responses)
        object.__setattr__(self, "runs", [])

    @override
    def run(
        self,
        args: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        input_text: str | None = None,
    ) -> CommandResult:
        del cwd, env, input_text
        command = tuple(args)
        self.runs.append(command)
        returncode, stdout, stderr = self.responses.get(command, (0, "", ""))
        return CommandResult(argv=self.argv(args), returncode=returncode, stdout=stdout, stderr=stderr)


def launcher_paths(workspace: TempLauncherWorkspace) -> WorkspacePaths:
    return build_workspace_paths(workspace.path, script_path=SCRIPTS_DIR / "overlord")


def docker_presence_query(paths: WorkspacePaths) -> tuple[str, ...]:
    return (
        "container",
        "ls",
        "--all",
        "--filter",
        f"name={paths.identity.container_name}",
        "--format",
        "{{.Names}}",
    )


def docker_state_query(paths: WorkspacePaths) -> tuple[str, ...]:
    return ("inspect", "--format", "{{.State.Status}}", paths.identity.container_name)


def image_cleanup_commands(paths: WorkspacePaths) -> tuple[tuple[str, ...], ...]:
    image_ref = local_image_ref(paths)
    return (
        ("image", "inspect", image_ref),
        ("rmi", "-f", image_ref),
        ("image", "prune", "-f", "--filter", "dangling=true"),
    )


def valid_mount_inspect(workspace: Path) -> str:
    return json.dumps(
        [
            {
                "Mounts": [
                    {"Type": "bind", "Source": str(workspace), "Destination": "/workspace", "RW": True},
                    {
                        "Type": "bind",
                        "Source": str(workspace / ".overlord" / "opencode-data"),
                        "Destination": "/home/overlord/.local/share/opencode",
                        "RW": True,
                    },
                    {
                        "Type": "bind",
                        "Source": str(workspace / ".overlord" / "zsh-data"),
                        "Destination": "/home/overlord/.zsh_data",
                        "RW": True,
                    },
                ]
            }
        ]
    )


if __name__ == "__main__":
    _ = unittest.main()
