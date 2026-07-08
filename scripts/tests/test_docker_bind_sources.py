from __future__ import annotations

import socket
import sys
import unittest
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from harness import TempLauncherWorkspace


SCRIPTS_DIR: Final = Path(__file__).resolve().parents[1]

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from overlord_py.container_lifecycle import ensure_running  # noqa: E402
from overlord_py.docker_bind_sources import parse_mounts  # noqa: E402
from overlord_py.engine import CommandResult, ContainerEngine  # noqa: E402
from overlord_py.paths import WorkspacePaths, build_workspace_paths  # noqa: E402


class DockerBindSourceTests(unittest.TestCase):
    def test_parse_mounts_consumes_printf_mount_output_without_padding(self) -> None:
        mounts = parse_mounts(("/Users/loco/repos\t/workspace\n",))

        self.assertEqual(len(mounts), 1)
        self.assertEqual(mounts[0].source, Path("/Users/loco/repos"))
        self.assertEqual(mounts[0].destination, Path("/workspace"))

    def test_missing_container_uses_current_container_host_mount_sources_for_child_binds(self) -> None:
        with bind_source_workspace() as fixture:
            host_workspace = Path("/Users/loco/repos/Docker Socket Project")
            host_gitconfig = Path("/Users/loco/.gitconfig")
            host_ssh_dir = Path("/Users/loco/.ssh")
            engine = CurrentContainerMountEngine(
                target_container_name=fixture.paths.identity.container_name,
                current_container_name=socket.gethostname(),
                mount_lines=(
                    f"{host_workspace}\t{fixture.paths.workspace}\n",
                    f"{host_gitconfig}\t{fixture.home / '.gitconfig'}\n",
                    f"{host_ssh_dir}\t{fixture.home / '.ssh'}\n",
                ),
            )

            ensure_running(engine, fixture.paths, (), env={}, home=fixture.home)

            run = first_run_args(engine.runs)
            self.assertIn(f"{host_workspace}:/workspace:rw", run)
            self.assertIn(f"{host_workspace / '.overlord' / 'opencode-data'}:/home/overlord/.local/share/opencode", run)
            self.assertIn(f"{host_workspace / '.overlord' / 'zsh-data'}:/home/overlord/.zsh_data", run)
            self.assertIn(f"{host_gitconfig}:/home/overlord/.gitconfig:ro", run)
            self.assertIn(f"{host_ssh_dir}:/home/overlord/.ssh:ro", run)


@dataclass(frozen=True, slots=True)
class BindSourceFixture:
    workspace: TempLauncherWorkspace
    paths: WorkspacePaths
    home: Path


class bind_source_workspace:
    def __init__(self) -> None:
        self._workspace = TempLauncherWorkspace(workspace_name="Docker Socket Project")

    def __enter__(self) -> BindSourceFixture:
        workspace = self._workspace.__enter__()
        home = workspace.path.parent / "launcher-home"
        home.mkdir()
        (home / ".gitconfig").write_text("[user]\n", encoding="utf-8")
        (home / ".ssh").mkdir()
        return BindSourceFixture(
            workspace=workspace,
            paths=build_workspace_paths(workspace.path, script_path=SCRIPTS_DIR / "overlord"),
            home=home,
        )

    def __exit__(self, exc_type: type[BaseException] | None, exc_value: BaseException | None, traceback: object) -> None:
        self._workspace.__exit__(exc_type, exc_value, None)


@dataclass(frozen=True, slots=True)
class CurrentContainerMountEngine(ContainerEngine):
    target_container_name: str
    current_container_name: str
    mount_lines: tuple[str, ...]
    runs: list[list[str]]

    def __init__(self, *, target_container_name: str, current_container_name: str, mount_lines: tuple[str, ...]) -> None:
        ContainerEngine.__init__(self, "docker")
        object.__setattr__(self, "target_container_name", target_container_name)
        object.__setattr__(self, "current_container_name", current_container_name)
        object.__setattr__(self, "mount_lines", mount_lines)
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
            return self.inspect_response(args_list)
        return CommandResult(argv=self.argv(args_list), returncode=0, stdout="", stderr="")

    def inspect_response(self, args: list[str]) -> CommandResult:
        inspected_name = args[-1]
        if inspected_name == self.target_container_name:
            return CommandResult(argv=self.argv(args), returncode=1, stdout="", stderr="")
        if inspected_name == self.current_container_name:
            return CommandResult(argv=self.argv(args), returncode=0, stdout="".join(self.mount_lines), stderr="")
        return CommandResult(argv=self.argv(args), returncode=1, stdout="", stderr="")


def first_run_args(runs: Sequence[list[str]]) -> list[str]:
    for args in runs:
        if args[:1] == ["run"]:
            return args
    raise AssertionError("Missing docker run invocation")


if __name__ == "__main__":
    unittest.main()
