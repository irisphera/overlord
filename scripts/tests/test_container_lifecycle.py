from __future__ import annotations

import os
import json
import socket
import sys
import unittest
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from harness import CommandRecord, TempLauncherWorkspace


SCRIPTS_DIR: Final = Path(__file__).resolve().parents[1]

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from overlord_py.container_lifecycle import (  # noqa: E402
    LifecycleError,
    SETUP_SCRIPT_CONTAINER_PATH,
    build_container_run_args,
    container_state,
    ensure_image,
    ensure_running,
    fresh,
    purge,
)
from overlord_py.docker_bind_sources import MOUNT_FORMAT  # noqa: E402
from overlord_py.engine import CommandResult, ContainerEngine  # noqa: E402
from overlord_py.env_builder import build_environment_plan  # noqa: E402
from overlord_py.paths import WorkspacePaths, build_workspace_paths  # noqa: E402
from overlord_py.persisted_state_mounts import MountSafetyFailure  # noqa: E402


class ContainerLifecycleTests(unittest.TestCase):
    def test_ensure_image_builds_only_when_image_is_missing(self) -> None:
        with lifecycle_workspace(image_exists=False) as fixture:
            messages = ensure_image(fixture.engine, fixture.paths, env=fixture.runner_env)

            self.assertEqual(messages, (f"Building overlord image from {SCRIPTS_DIR.parent}...",))
            image_ref = f"localhost/{fixture.paths.identity.image_name}:latest"
            self.assertIn(["docker", "build", "--load", "-t", image_ref, str(SCRIPTS_DIR.parent)], argv_list(fixture.records()))

        with lifecycle_workspace(image_exists=True) as fixture:
            messages = ensure_image(fixture.engine, fixture.paths, env=fixture.runner_env)

            self.assertEqual(messages, ())
            self.assertNotIn("build", subcommands(fixture.records()))

    def test_container_state_maps_missing_and_existing_states(self) -> None:
        with lifecycle_workspace(state="missing") as fixture:
            self.assertEqual(container_state(fixture.engine, fixture.paths, env=fixture.runner_env), "missing")

        with lifecycle_workspace(state="exited") as fixture:
            self.assertEqual(container_state(fixture.engine, fixture.paths, env=fixture.runner_env), "exited")

    def test_missing_container_creates_with_mounts_ports_optional_git_and_setup(self) -> None:
        with lifecycle_workspace(state="missing", host_files=True, setup_script=True) as fixture:
            traversal_dir = fixture.paths.workspace / "needs-traversal"
            traversal_dir.mkdir()
            traversal_dir.chmod(0o600)
            external_dir = fixture.paths.workspace.parent / "external-tool-dir"
            external_dir.mkdir(mode=0o700)
            (fixture.paths.workspace / "linked-tool-dir").symlink_to(external_dir, target_is_directory=True)
            result = ensure_running(fixture.engine, fixture.paths, fixture.environment.exec_env_flags, env=fixture.runner_env, home=fixture.home)

            records = fixture.records()
            run = first_record(records, "run")["argv"]
            setup = [record["argv"] for record in records if "/workspace/setup-devcontainer.sh" in record["argv"]]

            self.assertEqual(result.state_before, "missing")
            self.assertTrue(result.setup_ran)
            self.assertIn(f"Creating container {fixture.paths.identity.container_name}...", result.messages)
            self.assertIn("==> Running repo-controlled devcontainer setup as root: /workspace/setup-devcontainer.sh", result.messages)
            self.assertIn(f"{fixture.paths.workspace}:/workspace:rw", run)
            self.assertIn("/var/run/docker.sock:/var/run/docker.sock", run)
            self.assertIn(f"{fixture.paths.state.opencode_data}:/home/overlord/.local/share/opencode", run)
            self.assertIn(f"{fixture.paths.state.zsh_data}:/home/overlord/.zsh_data", run)
            self.assertIn("0.0.0.0::4090", run)
            self.assertIn("--security-opt", run)
            self.assertIn("label=disable", run)
            self.assertIn("seccomp=unconfined", run)
            self.assertIn(f"{fixture.home / '.gitconfig'}:/home/overlord/.gitconfig:ro", run)
            self.assertIn(f"{fixture.home / '.ssh'}:/home/overlord/.ssh:ro", run)
            self.assertEqual(run[-3:], [f"localhost/{fixture.paths.identity.image_name}:latest", "sleep", "infinity"])
            self.assertEqual((fixture.paths.workspace / ".gitignore").read_text(encoding="utf-8"), ".overlord/\n")
            self.assertTrue(traversal_dir.stat().st_mode & 0o111)
            self.assertEqual(external_dir.stat().st_mode & 0o777, 0o700)
            self.assertTrue(setup)
            self.assertIn("env", setup[0])
            self.assertIn("-i", setup[0])
            self.assertIn("HOME=/root", setup[0])
            self.assertIn("DEBIAN_FRONTEND=noninteractive", setup[0])
            self.assertNotIn("sentinel-azure-secret", " ".join(setup[0]))
            self.assertTrue(any(record["argv"][1:3] == ["exec", fixture.paths.identity.container_name] and "chown -R overlord:overlord" in " ".join(record["argv"]) for record in records))

    def test_setup_script_failure_warns_repairs_ownership_and_continues(self) -> None:
        with lifecycle_workspace(state="missing", setup_script=True) as fixture:
            engine = SetupFailureEngine(fixture.paths.identity.container_name)

            result = ensure_running(engine, fixture.paths, fixture.environment.exec_env_flags, env=fixture.runner_env, home=fixture.home)

            self.assertTrue(result.setup_ran)
            self.assertIn("Warning: repo-controlled setup failed: /workspace/setup-devcontainer.sh", "\n".join(result.messages))
            self.assertIn("curl: (22) The requested URL returned error: 404", "\n".join(result.messages))
            self.assertTrue(any("chown -R overlord:overlord" in " ".join(args) for args in engine.runs))

    def test_missing_container_without_optional_mounts_or_setup_skips_repo_setup(self) -> None:
        with lifecycle_workspace(state="missing") as fixture:
            result = ensure_running(fixture.engine, fixture.paths, fixture.environment.exec_env_flags, env=fixture.runner_env, home=fixture.home)

            run = first_record(fixture.records(), "run")["argv"]

            self.assertFalse(result.setup_ran)
            self.assertIn("==> No setup-devcontainer.sh found; skipping repo setup.", result.messages)
            self.assertFalse(any(".gitconfig" in arg for arg in run))
            self.assertFalse(any("/home/overlord/.ssh" in arg for arg in run))

    def test_exited_container_starts_and_runs_setup_while_running_reuse_skips_setup(self) -> None:
        with lifecycle_workspace(state="exited", setup_script=True) as fixture:
            result = ensure_running(fixture.engine, fixture.paths, fixture.environment.exec_env_flags, env=fixture.runner_env, home=fixture.home)

            self.assertEqual(result.state_before, "exited")
            self.assertTrue(result.setup_ran)
            self.assertIn("start", subcommands(fixture.records()))

        with lifecycle_workspace(state="running", setup_script=True) as fixture:
            result = ensure_running(fixture.engine, fixture.paths, fixture.environment.exec_env_flags, env=fixture.runner_env, home=fixture.home)

            self.assertEqual(result.state_before, "running")
            self.assertFalse(result.setup_ran)
            self.assertNotIn("start", subcommands(fixture.records()))
            self.assertNotIn("run", subcommands(fixture.records()))
            self.assertFalse(any("/workspace/setup-devcontainer.sh" in record["argv"] for record in fixture.records()))

    def test_unexpected_container_state_raises_current_diagnostic(self) -> None:
        with lifecycle_workspace(state="paused") as fixture:
            with self.assertRaises(LifecycleError) as caught:
                ensure_running(fixture.engine, fixture.paths, fixture.environment.exec_env_flags, env=fixture.runner_env)

            self.assertEqual(caught.exception.status, 1)
            self.assertIn(f"Error: Container {fixture.paths.identity.container_name} is in unexpected state: paused", caught.exception.message)
            self.assertIn("Try: overlord fresh", caught.exception.message)
            self.assertNotIn("exec", subcommands(fixture.records()))

    def test_build_container_run_args_preserves_security_ports_and_environment_boundaries(self) -> None:
        with lifecycle_workspace(host_files=True) as fixture:
            args = build_container_run_args(fixture.paths, fixture.environment.exec_env_flags, home=fixture.home)

            self.assertEqual(args[0:2], ["-d", "--name"])
            self.assertIn("--add-host=host.docker.internal:host-gateway", args)
            self.assertIn("--security-opt", args)
            self.assertIn("0.0.0.0::4090", args)
            self.assertIn("HOME=/home/overlord", args)
            self.assertIn("AZURE_API_KEY=sentinel-azure-secret", args)
            self.assertFalse(any(forbidden in arg for arg in args for forbidden in ("8787", "sentinel-opencode-password")))

    def test_fresh_verifies_mounts_before_clearing_markers_and_removing_container(self) -> None:
        with lifecycle_workspace(state="running", image_exists=True) as fixture:
            sentinel = fixture.paths.state.root / "sentinel.txt"
            sentinel.parent.mkdir()
            sentinel.write_text("keep\n", encoding="utf-8")
            stale_pid = fixture.paths.state.opencode_data / "overlord-serve.pid"
            stale_log = fixture.paths.state.opencode_data / "overlord-serve.log"
            stale_pid.parent.mkdir(parents=True)
            stale_pid.write_text("123\n", encoding="utf-8")
            stale_log.write_text("log\n", encoding="utf-8")

            messages = fresh(fixture.engine, fixture.paths, env=fixture.runner_env)

            self.assertTrue(sentinel.exists())
            self.assertFalse(stale_pid.exists())
            self.assertFalse(stale_log.exists())
            self.assertIn(f"Removing container {fixture.paths.identity.container_name}...", messages)
            self.assertIn("Done. Run 'overlord' to start fresh.", messages)
            self.assertEqual(
                argv_list(fixture.records()),
                [
                    *preflight_argv(fixture.paths),
                    ["docker", "stop", fixture.paths.identity.container_name],
                    ["docker", "rm", fixture.paths.identity.container_name],
                ],
            )

    def test_fresh_inspect_failure_preserves_markers_and_prevents_destruction(self) -> None:
        scenarios = (("missing", None), ("running", "{"), ("running", '[{"Mounts":[]}]'))
        for state, raw_inspect_output in scenarios:
            with self.subTest(state=state, raw_inspect_output=raw_inspect_output), lifecycle_workspace(
                state=state,
                image_exists=True,
                raw_inspect_output=raw_inspect_output,
            ) as fixture:
                # Given
                stale_pid = fixture.paths.state.opencode_data / "overlord-serve.pid"
                stale_log = fixture.paths.state.opencode_data / "overlord-serve.log"
                stale_pid.parent.mkdir(parents=True)
                stale_pid.write_text("123\n", encoding="utf-8")
                stale_log.write_text("log\n", encoding="utf-8")

                # When / Then
                with self.assertRaises(MountSafetyFailure):
                    _ = fresh(fixture.engine, fixture.paths, env=fixture.runner_env)
                self.assertTrue(stale_pid.exists())
                self.assertTrue(stale_log.exists())
                self.assertEqual(argv_list(fixture.records()), preflight_argv(fixture.paths))

    def test_purge_removes_image_prunes_and_preserves_state_sentinel(self) -> None:
        with lifecycle_workspace(state="running", image_exists=True) as fixture:
            sentinel = fixture.paths.state.root / "sentinel.txt"
            sentinel.parent.mkdir()
            sentinel.write_text("keep\n", encoding="utf-8")

            messages = purge(fixture.engine, fixture.paths, env=fixture.runner_env)

            self.assertTrue(sentinel.exists())
            self.assertIn(f"==> Removing image {fixture.paths.identity.image_name}...", messages)
            self.assertIn("==> Done. Run 'overlord' to rebuild and launch.", messages)
            image_ref = f"localhost/{fixture.paths.identity.image_name}:latest"
            self.assertEqual(
                argv_list(fixture.records()),
                [
                    *preflight_argv(fixture.paths),
                    ["docker", "rm", "-f", fixture.paths.identity.container_name],
                    ["docker", "image", "inspect", image_ref],
                    ["docker", "rmi", "-f", image_ref],
                    ["docker", "image", "prune", "-f", "--filter", "dangling=true"],
                ],
            )

    def test_purge_inspect_failure_prevents_every_destructive_command(self) -> None:
        scenarios = (("missing", None), ("running", "{"), ("running", '[{"Mounts":[]}]'))
        for state, raw_inspect_output in scenarios:
            with self.subTest(state=state, raw_inspect_output=raw_inspect_output), lifecycle_workspace(
                state=state,
                image_exists=True,
                raw_inspect_output=raw_inspect_output,
            ) as fixture:
                # When / Then
                with self.assertRaises(MountSafetyFailure):
                    _ = purge(fixture.engine, fixture.paths, env=fixture.runner_env)
                self.assertEqual(argv_list(fixture.records()), preflight_argv(fixture.paths))

    def test_purge_force_removes_stopped_container_before_image(self) -> None:
        with lifecycle_workspace(state="exited", image_exists=True) as fixture:
            purge(fixture.engine, fixture.paths, env=fixture.runner_env)

            self.assertIn(["docker", "rm", "-f", fixture.paths.identity.container_name], argv_list(fixture.records()))
            self.assertIn(["docker", "rmi", "-f", f"localhost/{fixture.paths.identity.image_name}:latest"], argv_list(fixture.records()))

    def test_purge_skips_absent_image_removal_and_still_prunes(self) -> None:
        # Given
        with lifecycle_workspace(state="running", image_exists=False) as fixture:
            image_ref = f"localhost/{fixture.paths.identity.image_name}:latest"

            # When
            messages = purge(fixture.engine, fixture.paths, env=fixture.runner_env)

            # Then
            self.assertIn(f"==> Image {fixture.paths.identity.image_name} is already absent.", messages)
            self.assertEqual(
                argv_list(fixture.records()),
                [
                    *preflight_argv(fixture.paths),
                    ["docker", "rm", "-f", fixture.paths.identity.container_name],
                    ["docker", "image", "inspect", image_ref],
                    ["docker", "image", "prune", "-f", "--filter", "dangling=true"],
                ],
            )

    def test_purge_failure_when_image_removal_fails_reports_runtime_error(self) -> None:
        with lifecycle_workspace(state="running", image_exists=True, rmi_fails=True) as fixture:
            with self.assertRaises(LifecycleError) as caught:
                purge(fixture.engine, fixture.paths, env=fixture.runner_env)

            self.assertEqual(caught.exception.message, "Error: failed to remove image: rmi failed")
            self.assertNotIn("prune", subcommands(fixture.records()))


class LifecycleFixture:
    def __init__(self, workspace: TempLauncherWorkspace, home: Path, paths: WorkspacePaths, runner_env: Mapping[str, str]) -> None:
        self.workspace = workspace
        self.home = home
        self.paths = paths
        self.runner_env = runner_env
        self.engine = ContainerEngine("docker")
        self.environment = build_environment_plan(
            {"HOME": str(home), "TERM": "xterm-256color", "AZURE_API_KEY": "sentinel-azure-secret", "OPENCODE_SERVER_PASSWORD": "sentinel-opencode-password"},
            home=home,
            workspace_name=paths.identity.workspace_name,
        )

    def records(self) -> list[CommandRecord]:
        return [record for record in self.workspace.read_command_log() if record["executable"] == "docker"]


class lifecycle_workspace:
    def __init__(
        self,
        *,
        state: str = "missing",
        image_exists: bool = True,
        host_files: bool = False,
        setup_script: bool = False,
        rmi_fails: bool = False,
        raw_inspect_output: str | None = None,
    ) -> None:
        self._state = state
        self._image_exists = image_exists
        self._host_files = host_files
        self._setup_script = setup_script
        self._rmi_fails = rmi_fails
        self._raw_inspect_output = raw_inspect_output
        self._workspace = TempLauncherWorkspace(workspace_name="Lifecycle Project")

    def __enter__(self) -> LifecycleFixture:
        workspace = self._workspace.__enter__()
        raw_inspect_output = valid_mount_inspect(workspace.path) if self._raw_inspect_output is None else self._raw_inspect_output
        workspace.install_fake_engine(
            "docker",
            state=self._state,
            image_exists=self._image_exists,
            rmi_fails=self._rmi_fails,
            raw_inspect_output=raw_inspect_output,
        )
        home = workspace.path / "host-home"
        home.mkdir()
        if self._host_files:
            (home / ".gitconfig").write_text("[user]\n", encoding="utf-8")
            (home / ".ssh").mkdir()
        if self._setup_script:
            setup = workspace.path / "setup-devcontainer.sh"
            setup.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
        paths = build_workspace_paths(workspace.path, script_path=SCRIPTS_DIR / "overlord")
        runner_env = {
            "PATH": f"{workspace.fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
            "FAKE_COMMAND_LOG": str(workspace.log_path),
            "FAKE_CAPTURE_ENV": "AZURE_API_KEY,EXA_API_KEY",
        }
        return LifecycleFixture(workspace, home, paths, runner_env)

    def __exit__(self, exc_type: type[BaseException] | None, exc_value: BaseException | None, traceback: object) -> None:
        self._workspace.__exit__(exc_type, exc_value, None)


@dataclass(frozen=True, slots=True)
class SetupFailureEngine(ContainerEngine):
    runs: list[list[str]]

    def __init__(self, _container_name: str) -> None:
        ContainerEngine.__init__(self, "docker")
        object.__setattr__(self, "runs", [])

    def run(
        self,
        args: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        input_text: str | None = None,
    ) -> CommandResult:
        del cwd, env, input_text
        args_list = [*args]
        self.runs.append(args_list)
        if args_list[:2] == ["inspect", "--format"]:
            return CommandResult(argv=self.argv(args_list), returncode=1, stdout="", stderr="")
        if SETUP_SCRIPT_CONTAINER_PATH in args_list:
            return CommandResult(argv=self.argv(args_list), returncode=22, stdout="", stderr="curl: (22) The requested URL returned error: 404\n")
        return CommandResult(argv=self.argv(args_list), returncode=0, stdout="", stderr="")


def subcommands(records: list[CommandRecord]) -> list[str]:
    return [record["argv"][1] for record in records if len(record["argv"]) > 1]


def argv_list(records: list[CommandRecord]) -> list[list[str]]:
    return [record["argv"] for record in records]


def first_record(records: list[CommandRecord], subcommand: str) -> CommandRecord:
    for record in records:
        if len(record["argv"]) > 1 and record["argv"][1] == subcommand:
            return record
    raise AssertionError(f"Missing engine subcommand: {subcommand}")


def preflight_argv(paths: WorkspacePaths) -> list[list[str]]:
    return [
        ["docker", "inspect", "--format", MOUNT_FORMAT, socket.gethostname()],
        ["docker", "inspect", paths.identity.container_name],
    ]


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
    unittest.main()
