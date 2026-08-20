from __future__ import annotations

from collections.abc import Mapping, Sequence
import os
from pathlib import Path
import sys
from typing import Final, assert_never

from overlord_py.cli import Command, parse_cli
from overlord_py.container_lifecycle import LifecycleError, ensure_image, ensure_running, fresh, purge
from overlord_py.engine import ContainerEngine, EngineDetectionError, detect_engine
from overlord_py.env_builder import build_environment_plan, normalized_host_env
from overlord_py.paths import GitdirOutsideWorkspaceError, WorkspacePaths, build_workspace_paths, ensure_gitdir_within_workspace
from overlord_py.persisted_state_mounts import MountSafetyFailure
from overlord_py.progress import stdout_stage
from overlord_py.runtime_config import inject_initial_runtime_config
from overlord_py.state import ensure_state_dir
from overlord_py.terminal import run_terminal_command, terminal_title

RESPONSIBILITY: Final = "compose parser, lifecycle, runtime repair, and final command dispatch"
MOUNT_SAFETY_FAILURE_MESSAGE: Final = (
    "Error: mount-safety check failed; the destructive operation was refused. "
    "Persisted state was not changed. Resolve the reported mount problem before retrying.\n"
)

def main(argv: Sequence[str] | None = None) -> int:
    args = tuple(sys.argv[1:] if argv is None else argv)
    host_env = dict(os.environ)
    paths = build_workspace_paths(Path.cwd(), script_path=Path(__file__).resolve().parents[1] / "overlord")
    try:
        engine = detect_engine(path_env=host_env.get("PATH"))
        result = parse_cli(args, env=host_env, repo_root=paths.repo_root)
        write_streams(result.stdout, result.stderr)
        if result.status != 0 or result.options is None:
            return result.status
        return run_launcher(engine, paths, result.options, host_env)
    except GitdirOutsideWorkspaceError as error:
        _ = sys.stderr.write(f"{error}\n")
        return 1
    except MountSafetyFailure as error:
        sys.stderr.write(f"{MOUNT_SAFETY_FAILURE_MESSAGE}Details: {error}\n")
        return 1
    except (EngineDetectionError, LifecycleError, RuntimeError) as error:
        sys.stderr.write(str(error))
        if not str(error).endswith("\n"):
            sys.stderr.write("\n")
        return getattr(error, "status", 1)

def run_launcher(engine: ContainerEngine, paths: WorkspacePaths, options, host_env: Mapping[str, str]) -> int:
    match options.command:
        case Command.FRESH:
            write_messages(fresh(engine, paths, env=host_env, stage=stdout_stage, after_verification=lambda: None))
            return 0
        case Command.PURGE:
            write_messages(purge(engine, paths, env=host_env, stage=stdout_stage, after_verification=lambda: None))
            return 0
        case Command.SHELL | Command.ZELLIJ:
            return run_container_command(engine, paths, options, host_env)
        case Command.HELP:
            return 0
        case unreachable:
            assert_never(unreachable)

def run_container_command(engine: ContainerEngine, paths: WorkspacePaths, options, host_env: Mapping[str, str]) -> int:
    ensure_gitdir_within_workspace(paths)
    _ = ensure_state_dir(paths.state)
    write_messages(ensure_image(engine, paths, env=host_env, stage=stdout_stage))
    home = Path(host_env.get("HOME", str(Path.home())))
    environment = build_environment_plan(host_env, home=home, workspace_name=paths.identity.workspace_name)
    runner_env = normalized_host_env(host_env)
    running = ensure_running(engine, paths, environment.exec_env_flags, env=runner_env, home=home, stage=stdout_stage)
    write_messages(running.messages)
    if running.state_before != "running":
        inject_initial_runtime_config(engine, paths, env=runner_env)
    sys.stdout.write(terminal_title(paths.identity.zellij_session))
    return dispatch_final(engine, paths, environment, options, runner_env)

def dispatch_final(engine: ContainerEngine, paths: WorkspacePaths, environment, options, env: Mapping[str, str]) -> int:
    match options.command:
        case Command.SHELL:
            stdout_stage(f"Opening shell in {paths.identity.container_name}...")
            return run_terminal_command(engine, paths, environment.exec_env_flags, "shell", env=env)
        case Command.ZELLIJ:
            stdout_stage(f"Opening zellij in {paths.identity.container_name}...")
            return run_terminal_command(engine, paths, environment.exec_env_flags, "zellij", env=env)
        case _:
            return 1

def write_streams(stdout: str | None, stderr: str | None) -> None:
    if stdout:
        sys.stdout.write(stdout)
    if stderr:
        sys.stderr.write(stderr)

def write_messages(messages: Sequence[str]) -> None:
    for msg in messages:
        if msg:
            sys.stdout.write(msg if msg.endswith("\n") else msg + "\n")
