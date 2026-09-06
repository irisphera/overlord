from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from dataclasses import replace
import fcntl
import os
from pathlib import Path
import sys
from typing import Final, assert_never

from overlord_py.cli import Command, parse_cli
from overlord_py.container_lifecycle import LifecycleError, ensure_running, fresh, purge
from overlord_py.engine import ContainerEngine, EngineDetectionError, detect_engine
from overlord_py.env_builder import build_environment_plan
from overlord_py.docker_bind_sources import validate_local_endpoint
from overlord_py.paths import GitdirOutsideWorkspaceError, WorkspacePaths, build_workspace_paths, ensure_gitdir_within_workspace
from overlord_py.persisted_state_mounts import MountSafetyFailure
from overlord_py.progress import restore_sane_tty, stdout_stage
from overlord_py.terminal import run_terminal_command, terminal_title

MOUNT_SAFETY_FAILURE_MESSAGE: Final = (
    "Error: mount-safety check failed; the operation was refused. "
    "Persisted state was not changed. Resolve the reported mount problem before retrying.\n"
)

def main(argv: Sequence[str] | None = None) -> int:
    restore_sane_tty()
    args = tuple(sys.argv[1:] if argv is None else argv)
    host_env = dict(os.environ)
    paths = build_workspace_paths(Path.cwd(), script_path=Path(__file__).resolve().parents[1] / "overlord")
    try:
        result = parse_cli(args, env=host_env, repo_root=paths.repo_root)
        write_streams(result.stdout, result.stderr)
        if result.status != 0 or result.options is None:
            return result.status
        if result.options.command is Command.HELP:
            return 0
        engine = detect_engine(path_env=host_env.get("PATH"))
        return run_launcher(engine, paths, result.options, host_env)
    except GitdirOutsideWorkspaceError as error:
        _ = sys.stderr.write(f"{error}\n")
        return 1
    except MountSafetyFailure as error:
        sys.stderr.write(f"{MOUNT_SAFETY_FAILURE_MESSAGE}Details: {error}\n")
        return 1
    except (EngineDetectionError, LifecycleError, RuntimeError, OSError) as error:
        sys.stderr.write(str(error))
        if not str(error).endswith("\n"):
            sys.stderr.write("\n")
        return getattr(error, "status", 1)

@contextmanager
def workspace_lock(workspace: Path):
    # Lock the canonical directory inode: no state writes or removable lock-file race.
    descriptor = os.open(workspace, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)

def run_launcher(engine: ContainerEngine, paths: WorkspacePaths, options, host_env: Mapping[str, str]) -> int:
    validate_local_endpoint(engine, paths, env=host_env)
    match options.command:
        case Command.FRESH:
            with workspace_lock(paths.workspace):
                write_messages(fresh(engine, paths, env=host_env, stage=stdout_stage))
            return 0
        case Command.PURGE:
            with workspace_lock(paths.workspace):
                write_messages(purge(engine, paths, env=host_env, stage=stdout_stage))
            return 0
        case Command.SHELL | Command.ZELLIJ:
            return run_container_command(engine, paths, options, host_env)
        case Command.HELP:
            return 0
        case unreachable:
            assert_never(unreachable)

def run_container_command(engine: ContainerEngine, paths: WorkspacePaths, options, host_env: Mapping[str, str]) -> int:
    ensure_gitdir_within_workspace(paths)
    home = Path(host_env.get("HOME", str(Path.home())))
    environment = build_environment_plan(host_env, home=home, workspace_name=paths.identity.workspace_name)
    with workspace_lock(paths.workspace):
        running = ensure_running(engine, paths, environment.exec_env_flags, env=host_env, home=home, stage=stdout_stage)
        write_messages(running.messages)
        target = replace(paths, identity=replace(paths.identity, container_name=running.container_id))
    # The interactive terminal never holds the lifecycle lock.
    sys.stdout.write(terminal_title(paths.identity.zellij_session))
    return dispatch_final(engine, target, environment, options, host_env)

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

if __name__ == "__main__":
    raise SystemExit(main())
