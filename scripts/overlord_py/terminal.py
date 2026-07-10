"""Interactive shell and zellij dispatch seam."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
import subprocess
from typing import Final, Literal, assert_never

from overlord_py.engine import ContainerEngine
from overlord_py.paths import WorkspacePaths

RESPONSIBILITY: Final = "enter shell or zellij with the existing container exec command shape"
EXEC_USER: Final = "overlord"
TerminalCommand = Literal["shell", "zellij"]


def describe() -> str:
    return RESPONSIBILITY


def terminal_title(workspace_name: str) -> str:
    return f"\033]0;{workspace_name}\007"


def terminal_exec_args(paths: WorkspacePaths, exec_env_flags: Sequence[str], command: TerminalCommand) -> list[str]:
    match command:
        case "shell":
            return [
                "exec",
                "-it",
                "-w",
                "/workspace",
                "-u",
                EXEC_USER,
                *exec_env_flags,
                "-e",
                "OPENCODE_SERVER_PASSWORD=",
                paths.identity.container_name,
                "zsh",
                "-il",
            ]
        case "zellij":
            return [
                "exec",
                "-it",
                "-u",
                EXEC_USER,
                *exec_env_flags,
                "-e",
                "OPENCODE_SERVER_PASSWORD=",
                paths.identity.container_name,
                "zellij",
                "attach",
                paths.identity.zellij_session,
                "--create",
            ]
        case unreachable:
            assert_never(unreachable)


def run_terminal_command(
    engine: ContainerEngine,
    paths: WorkspacePaths,
    exec_env_flags: Sequence[str],
    command: TerminalCommand,
    *,
    env: Mapping[str, str],
) -> int:
    completed = subprocess.run(
        engine.argv(terminal_exec_args(paths, exec_env_flags, command)),
        cwd=paths.workspace,
        env=dict(env),
        check=False,
    )
    return completed.returncode
