from __future__ import annotations

import unittest
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from harness import CommandRecord, HarnessRun, TempLauncherWorkspace
from test_orchestrator import http_fixture


LAUNCHER: Final = Path(__file__).resolve().parents[1] / "overlord"
UTILITY_COMMANDS: Final = ("bash", "python3", "readlink", "dirname", "basename", "tr", "sed", "grep", "mkdir", "chmod", "rm", "cat")


@dataclass(frozen=True, slots=True)
class RunShape:
    argv: list[str]
    workspace_path: Path
    container: str
    image: str
    workspace_name: str


class LifecycleCharacterizationTests(unittest.TestCase):
    def test_default_web_creates_missing_docker_container_with_current_run_shape(self) -> None:
        with launcher_workspace(workspace_name="My Project!") as workspace, http_fixture() as server:
            workspace.install_fake_engine("docker", state="missing", image_exists=False, port_output=server.port_output)

            result = run_launcher(workspace, env=host_env(workspace))

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Building overlord image", result.stdout)
            self.assertIn("Creating container overlord-my-project-", result.stdout)
            self.assertIn(f"Local access:   http://localhost:{server.port}", result.stdout)
            self.assertEqual(result.stderr, "")
            self.assert_state_dir(workspace)
            self.assertEqual((workspace.path / ".gitignore").read_text(encoding="utf-8"), ".overlord/\n")
            docker = engine_records(workspace, "docker")
            self.assertEqual(docker[0]["argv"], ["docker", "image", "inspect", "localhost/overlord-opencode-my-project-:latest"])
            self.assertIn(["docker", "build", "--load", "-t", "localhost/overlord-opencode-my-project-:latest", str(Path(__file__).resolve().parents[2])], argv_list(docker))
            run = first_record(docker, "run")
            self.assert_run_shape(RunShape(run["argv"], workspace.path, "overlord-my-project-", "localhost/overlord-opencode-my-project-:latest", "My Project!"))
            self.assertLess(index_of(docker, "build"), index_of(docker, "run"))
            self.assertGreater(index_of(docker, "port"), index_of(docker, "run"))

    def test_web_and_opencode_aliases_reuse_running_container_for_web_server(self) -> None:
        for command in ("web", "opencode"):
            with self.subTest(command=command), launcher_workspace() as workspace, http_fixture() as server:
                workspace.install_fake_engine("docker", state="running", image_exists=True, port_output=server.port_output)

                result = run_launcher(workspace, command, env=host_env(workspace))

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("==> Ensuring OpenCode web server is running", result.stdout)
                docker = engine_records(workspace, "docker")
                self.assertNotIn("run", subcommands(docker))
                self.assertNotIn("start", subcommands(docker))
                self.assertIn("port", subcommands(docker))
                self.assertTrue(any("/home/overlord/.local/share/opencode/overlord-serve.pid" in record["argv"] for record in docker))

    def test_running_web_reuse_skips_setup_devcontainer_even_when_script_exists(self) -> None:
        with launcher_workspace() as workspace, http_fixture() as server:
            workspace.install_fake_engine("docker", state="running", image_exists=True, port_output=server.port_output)
            (workspace.path / "setup-devcontainer.sh").write_text("#!/usr/bin/env bash\ntouch setup-ran\n", encoding="utf-8")

            result = run_launcher(workspace, "web", env=host_env(workspace))

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(f"Local access:   http://localhost:{server.port}", result.stdout)
            self.assertNotIn("Running repo-controlled devcontainer setup", result.stdout)
            docker = engine_records(workspace, "docker")
            self.assertFalse(any("/workspace/setup-devcontainer.sh" in record["argv"] for record in docker))

    def test_shell_and_zellij_use_terminal_exec_shapes_and_workspace_basename(self) -> None:
        expectations = {
            "shell": ["-it", "-w", "/workspace", "-u", "overlord", "overlord-my-project-", "zsh", "-il"],
            "zellij": ["-it", "-u", "overlord", "overlord-my-project-", "zellij", "attach", "My Project!", "--create"],
        }
        for command, expected_tail in expectations.items():
            with self.subTest(command=command), launcher_workspace(workspace_name="My Project!") as workspace:
                workspace.install_fake_engine("docker", state="running", image_exists=True)

                result = run_launcher(workspace, command, env=host_env(workspace))

                self.assertEqual(result.returncode, 0, result.stderr)
                final_exec = engine_records(workspace, "docker")[-1]["argv"]
                self.assertEqual(final_exec[0:2], ["docker", "exec"])
                self.assert_contains_ordered(final_exec, expected_tail)
                self.assertIn("OVERLORD_WORKSPACE=My Project!", final_exec)
                self.assertIn("HEADROOM_TELEMETRY=off", final_exec)

    def test_fresh_and_purge_preserve_overlord_state_and_issue_removal_commands(self) -> None:
        for command in ("fresh", "purge"):
            with self.subTest(command=command), launcher_workspace() as workspace:
                workspace.install_fake_engine("docker", state="running", image_exists=True)
                sentinel = workspace.path / ".overlord" / "sentinel.txt"
                sentinel.parent.mkdir()
                sentinel.write_text("keep\n", encoding="utf-8")

                result = run_launcher(workspace, command, env=host_env(workspace))

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(sentinel.exists())
                docker = engine_records(workspace, "docker")
                self.assertIn("cp", subcommands(docker))
                self.assertIn("rm", subcommands(docker))
                if command == "purge":
                    self.assertNotIn("stop", subcommands(docker))
                    self.assertTrue(any(record["argv"][1:3] == ["rm", "-f"] for record in docker))
                    self.assertIn("rmi", subcommands(docker))
                    self.assertIn(["docker", "image", "prune", "-f", "--filter", "dangling=true"], argv_list(docker))
                else:
                    self.assertIn("stop", subcommands(docker))
                    self.assertNotIn("rmi", subcommands(docker))

    def test_engine_selection_prefers_podman_and_falls_back_to_docker_only(self) -> None:
        with launcher_workspace() as both_workspace:
            both_workspace.install_fake_engine("podman", state="running", image_exists=True)
            both_workspace.install_fake_engine("docker", state="running", image_exists=True)

            both_result = run_launcher(both_workspace, "shell", env=host_env(both_workspace))

            self.assertEqual(both_result.returncode, 0, both_result.stderr)
            self.assertGreater(len(engine_records(both_workspace, "podman")), 0)
            self.assertEqual(engine_records(both_workspace, "docker"), [])

        with launcher_workspace() as docker_workspace:
            docker_workspace.install_fake_engine("docker", state="running", image_exists=True)

            docker_result = run_launcher(docker_workspace, "shell", env=host_env(docker_workspace))

            self.assertEqual(docker_result.returncode, 0, docker_result.stderr)
            self.assertGreater(len(engine_records(docker_workspace, "docker")), 0)

    def test_container_states_and_image_presence_follow_current_lifecycle_branches(self) -> None:
        scenarios = (("running", "shell", "exec"), ("exited", "shell", "start"), ("missing", "shell", "run"))
        for state, command, expected_subcommand in scenarios:
            with self.subTest(state=state), launcher_workspace() as workspace:
                workspace.install_fake_engine("docker", state=state, image_exists=True)

                result = run_launcher(workspace, command, env=host_env(workspace))

                self.assertEqual(result.returncode, 0, result.stderr)
                docker = engine_records(workspace, "docker")
                self.assertIn(expected_subcommand, subcommands(docker))
                self.assertNotIn("build", subcommands(docker))

        with launcher_workspace() as workspace:
            workspace.install_fake_engine("docker", state="paused", image_exists=True)

            result = run_launcher(workspace, "shell", env=host_env(workspace))

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Error: Container overlord-", result.stderr)
            self.assertIn("is in unexpected state: paused", result.stderr)
            self.assertIn("Try: overlord fresh", result.stderr)
            self.assertNotIn("exec", subcommands(engine_records(workspace, "docker")))

    def test_gitignore_append_only_behavior_does_not_duplicate_existing_state_entry(self) -> None:
        with launcher_workspace() as workspace:
            workspace.install_fake_engine("docker", state="missing", image_exists=True)
            gitignore = workspace.path / ".gitignore"
            gitignore.write_text("keep-me\n.overlord/\n", encoding="utf-8")

            result = run_launcher(workspace, "shell", env=host_env(workspace))

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(gitignore.read_text(encoding="utf-8"), "keep-me\n.overlord/\n")

    def test_missing_engine_uses_current_error_without_real_podman_or_docker(self) -> None:
        with launcher_workspace() as workspace:
            result = run_launcher(workspace, "help", env=host_env(workspace))

            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, "")
            self.assertEqual(result.stderr, "Error: neither podman nor docker found in PATH\n")
            self.assertEqual(workspace.read_command_log(), [])

    def assert_state_dir(self, workspace: TempLauncherWorkspace) -> None:
        self.assertTrue((workspace.path / ".overlord" / "opencode-data").is_dir())
        self.assertTrue((workspace.path / ".overlord" / "zsh-data").is_dir())

    def assert_run_shape(self, shape: RunShape) -> None:
        argv = shape.argv
        self.assertEqual(argv[0:2], ["docker", "run"])
        self.assertIn("-d", argv)
        self.assert_contains_ordered(argv, ["--name", shape.container])
        self.assertIn("--add-host=host.docker.internal:host-gateway", argv)
        self.assertIn("--security-opt", argv)
        self.assertIn("label=disable", argv)
        self.assertIn("seccomp=unconfined", argv)
        self.assertIn(f"{shape.workspace_path}:/workspace:rw", argv)
        self.assertIn("/var/run/docker.sock:/var/run/docker.sock", argv)
        self.assertIn(f"{shape.workspace_path}/.overlord/opencode-data:/home/overlord/.local/share/opencode", argv)
        self.assertIn(f"{shape.workspace_path}/.overlord/zsh-data:/home/overlord/.zsh_data", argv)
        self.assertIn("0.0.0.0::4090", argv)
        self.assertIn(f"OVERLORD_WORKSPACE={shape.workspace_name}", argv)
        self.assertIn("HEADROOM_TELEMETRY=off", argv)
        self.assertEqual(argv[-3:], [shape.image, "sleep", "infinity"])

    def assert_contains_ordered(self, values: list[str], expected: list[str]) -> None:
        cursor = 0
        for item in expected:
            cursor = values.index(item, cursor) + 1


@contextmanager
def launcher_workspace(workspace_name: str | None = None) -> Iterator[TempLauncherWorkspace]:
    with TempLauncherWorkspace(workspace_name=workspace_name) as workspace:
        for command in UTILITY_COMMANDS:
            workspace.install_passthrough_command(command)
        yield workspace


def run_launcher(
    workspace: TempLauncherWorkspace,
    *args: str,
    env: Mapping[str, str],
) -> HarnessRun:
    return workspace.run_launcher(LAUNCHER, args=args, env=dict(env), system_path="")


def host_env(workspace: TempLauncherWorkspace) -> dict[str, str]:
    home = workspace.path / "host-home"
    home.mkdir(exist_ok=True)
    return {"HOME": str(home), "TERM": "xterm-256color", "AZURE_API_KEY": "sentinel-azure", "EXA_API_KEY": "sentinel-exa"}


def engine_records(workspace: TempLauncherWorkspace, executable: str) -> list[CommandRecord]:
    return [record for record in workspace.read_command_log() if record["executable"] == executable]


def subcommands(records: Iterable[CommandRecord]) -> list[str]:
    return [record["argv"][1] for record in records if len(record["argv"]) > 1]


def argv_list(records: Iterable[CommandRecord]) -> list[list[str]]:
    return [record["argv"] for record in records]


def first_record(records: Iterable[CommandRecord], subcommand: str) -> CommandRecord:
    for record in records:
        if len(record["argv"]) > 1 and record["argv"][1] == subcommand:
            return record
    raise AssertionError(f"Missing engine subcommand: {subcommand}")


def index_of(records: list[CommandRecord], subcommand: str) -> int:
    for index, record in enumerate(records):
        if len(record["argv"]) > 1 and record["argv"][1] == subcommand:
            return index
    raise AssertionError(f"Missing engine subcommand: {subcommand}")


if __name__ == "__main__":
    unittest.main()
