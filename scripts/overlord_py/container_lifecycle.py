"""Container image, create, reuse, fresh, and purge lifecycle seam."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import os
from pathlib import Path
from typing import Final

from overlord_py.engine import CommandResult, ContainerEngine
from overlord_py.paths import WorkspacePaths
from overlord_py.state import clear_persisted_opencode_server_state, ensure_state_dir

RESPONSIBILITY: Final = "preserve image/container lifecycle, mounts, setup timing, and removal semantics"
OPENCODE_WEB_PORT: Final = "4090"
CONTAINER_HOME: Final = "/home/overlord"
SETUP_SCRIPT_CONTAINER_PATH: Final = "/workspace/setup-devcontainer.sh"
ROOT_SETUP_ENV: Final = (
    "HOME=/root",
    "USER=root",
    "LOGNAME=root",
    "DEBIAN_FRONTEND=noninteractive",
    "npm_config_cache=/root/.npm",
    "UV_CACHE_DIR=/root/.cache/uv",
    "PATH=/usr/local/.safe-chain/shims:/usr/local/.safe-chain/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/home/overlord/.bun/bin:/home/overlord/.local/bin",
)
SETUP_OWNERSHIP_REPAIR_SCRIPT: Final = """
    chown -R overlord:overlord \
        /home/overlord/.cache \
        /home/overlord/.config \
        /home/overlord/.local \
        /home/overlord/.npm \
        2>/dev/null || true
    chmod -R a+rwX \
        /home/overlord/.cache \
        /home/overlord/.config \
        /home/overlord/.local \
        /home/overlord/.npm \
        2>/dev/null || true
"""


@dataclass(frozen=True, slots=True)
class LifecycleError(Exception):
    message: str
    status: int = 1

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True, slots=True)
class EnsureRunningResult:
    state_before: str
    setup_ran: bool
    messages: tuple[str, ...]


def describe() -> str:
    return RESPONSIBILITY


def local_image_ref(paths: WorkspacePaths) -> str:
    return f"localhost/{paths.identity.image_name}:latest"


def ensure_image(engine: ContainerEngine, paths: WorkspacePaths, *, env: Mapping[str, str]) -> tuple[str, ...]:
    image = engine.run(["image", "inspect", local_image_ref(paths)], cwd=paths.workspace, env=env)
    if image.returncode == 0:
        return ()
    message = f"Building overlord image from {paths.repo_root}..."
    build_args = ["build", *(("--load",) if engine.name == "docker" else ()), "-t", local_image_ref(paths), str(paths.repo_root)]
    build = engine.run(build_args, cwd=paths.workspace, env=env)
    require_success(build, "build image")
    return (message,)


def container_state(engine: ContainerEngine, paths: WorkspacePaths, *, env: Mapping[str, str]) -> str:
    result = engine.run(
        ["inspect", "--format", "{{.State.Status}}", paths.identity.container_name],
        cwd=paths.workspace,
        env=env,
    )
    if result.returncode != 0:
        return "missing"
    state = result.stdout.strip()
    if state:
        return state
    return "missing"


def ensure_running(
    engine: ContainerEngine,
    paths: WorkspacePaths,
    exec_env_flags: Sequence[str],
    *,
    env: Mapping[str, str],
    home: Path | None = None,
) -> EnsureRunningResult:
    state = container_state(engine, paths, env=env)
    messages: list[str] = []
    setup_allowed = True
    match state:
        case "missing":
            messages.append(f"Creating container {paths.identity.container_name}...")
            ensure_state_dir(paths.state)
            run = engine.run(
                ["run", *build_container_run_args(paths, exec_env_flags, home=home), local_image_ref(paths), "sleep", "infinity"],
                cwd=paths.workspace,
                env=env,
            )
            require_success(run, "create container")
        case "exited":
            messages.append(f"Starting container {paths.identity.container_name}...")
            start = engine.run(["start", paths.identity.container_name], cwd=paths.workspace, env=env)
            require_success(start, "start container")
        case "running":
            setup_allowed = False
        case _:
            raise LifecycleError(
                f"Error: Container {paths.identity.container_name} is in unexpected state: {state}\nTry: overlord fresh"
            )
    if not setup_allowed:
        return EnsureRunningResult(state_before=state, setup_ran=False, messages=tuple(messages))
    chmod_workspace_for_rootless_podman(paths.workspace)
    setup_messages, setup_ran = run_workspace_setup_script(engine, paths, env=env)
    messages.extend(setup_messages)
    return EnsureRunningResult(state_before=state, setup_ran=setup_ran, messages=tuple(messages))


def build_container_run_args(
    paths: WorkspacePaths,
    exec_env_flags: Sequence[str],
    *,
    home: Path | None = None,
) -> list[str]:
    host_home = Path.home() if home is None else home
    args = [
        "-d",
        "--name",
        paths.identity.container_name,
        "--add-host=host.docker.internal:host-gateway",
        "--security-opt",
        "label=disable",
        "--security-opt",
        "seccomp=unconfined",
        "-v",
        f"{paths.workspace}:/workspace:rw",
        "-v",
        "/var/run/docker.sock:/var/run/docker.sock",
        "-v",
        f"{paths.state.opencode_data}:/home/overlord/.local/share/opencode",
        "-v",
        f"{paths.state.zsh_data}:/home/overlord/.zsh_data",
        "-p",
        f"0.0.0.0::{OPENCODE_WEB_PORT}",
        *exec_env_flags,
    ]
    gitconfig = host_home / ".gitconfig"
    ssh_dir = host_home / ".ssh"
    if gitconfig.is_file():
        args.extend(("-v", f"{gitconfig}:/home/overlord/.gitconfig:ro"))
    if ssh_dir.is_dir():
        args.extend(("-v", f"{ssh_dir}:/home/overlord/.ssh:ro"))
    return args


def fresh(engine: ContainerEngine, paths: WorkspacePaths, *, env: Mapping[str, str]) -> tuple[str, ...]:
    messages = [*backup_container_data(engine, paths, env=env)]
    clear_persisted_opencode_server_state(paths.state)
    messages.append(f"Removing container {paths.identity.container_name}...")
    ignore_failure(engine.run(["stop", paths.identity.container_name], cwd=paths.workspace, env=env))
    ignore_failure(engine.run(["rm", paths.identity.container_name], cwd=paths.workspace, env=env))
    messages.append("Done. Run 'overlord' to start fresh.")
    return tuple(messages)


def purge(engine: ContainerEngine, paths: WorkspacePaths, *, env: Mapping[str, str]) -> tuple[str, ...]:
    messages = [*backup_container_data(engine, paths, env=env)]
    messages.append(f"==> Removing container {paths.identity.container_name}...")
    ignore_failure(engine.run(["stop", paths.identity.container_name], cwd=paths.workspace, env=env))
    ignore_failure(engine.run(["rm", paths.identity.container_name], cwd=paths.workspace, env=env))
    image_ref = local_image_ref(paths)
    image = engine.run(["image", "inspect", image_ref], cwd=paths.workspace, env=env)
    if image.returncode == 0:
        messages.append(f"==> Removing image {paths.identity.image_name}...")
        remove = engine.run(["rmi", image_ref], cwd=paths.workspace, env=env)
        if remove.returncode != 0:
            messages.append("Warning: image removal command reported a failure; verifying final image state...")
        final_image = engine.run(["image", "inspect", image_ref], cwd=paths.workspace, env=env)
        if final_image.returncode == 0:
            messages.append(f"Error: image {paths.identity.image_name} still exists after purge; rebuild is not guaranteed.")
            raise LifecycleError("\n".join(messages))
    else:
        messages.append(f"==> Image {paths.identity.image_name} is already absent.")
    messages.append("==> Pruning dangling images...")
    ignore_failure(engine.run(["image", "prune", "-f", "--filter", "dangling=true"], cwd=paths.workspace, env=env))
    messages.append("==> Done. Run 'overlord' to rebuild and launch.")
    return tuple(messages)


def backup_container_data(engine: ContainerEngine, paths: WorkspacePaths, *, env: Mapping[str, str]) -> tuple[str, ...]:
    inspect = engine.run(["inspect", paths.identity.container_name], cwd=paths.workspace, env=env)
    if inspect.returncode != 0:
        return ()
    paths.state.opencode_data.mkdir(parents=True, exist_ok=True)
    paths.state.zsh_data.mkdir(parents=True, exist_ok=True)
    ignore_failure(
        engine.run(
            ["cp", f"{paths.identity.container_name}:{CONTAINER_HOME}/.local/share/opencode/.", f"{paths.state.opencode_data}/"],
            cwd=paths.workspace,
            env=env,
        )
    )
    ignore_failure(
        engine.run(
            ["cp", f"{paths.identity.container_name}:{CONTAINER_HOME}/.zsh_data/.", f"{paths.state.zsh_data}/"],
            cwd=paths.workspace,
            env=env,
        )
    )
    return ("Backing up session data...",)


def run_workspace_setup_script(
    engine: ContainerEngine,
    paths: WorkspacePaths,
    *,
    env: Mapping[str, str],
) -> tuple[tuple[str, ...], bool]:
    if not (paths.workspace / "setup-devcontainer.sh").is_file():
        return (("==> No setup-devcontainer.sh found; skipping repo setup.",), False)
    setup = engine.run(
        [
            "exec",
            "-i",
            "-w",
            "/workspace",
            "-u",
            "root",
            paths.identity.container_name,
            "env",
            "-i",
            *ROOT_SETUP_ENV,
            "bash",
            SETUP_SCRIPT_CONTAINER_PATH,
        ],
        cwd=paths.workspace,
        env=env,
    )
    require_success(setup, "run workspace setup")
    repair_workspace_setup_ownership(engine, paths, env=env)
    return ((f"==> Running repo-controlled devcontainer setup as root: {SETUP_SCRIPT_CONTAINER_PATH}",), True)


def repair_workspace_setup_ownership(engine: ContainerEngine, paths: WorkspacePaths, *, env: Mapping[str, str]) -> None:
    ignore_failure(engine.run(["exec", paths.identity.container_name, "sh", "-c", SETUP_OWNERSHIP_REPAIR_SCRIPT], cwd=paths.workspace, env=env))


def chmod_workspace_for_rootless_podman(workspace: Path) -> None:
    for root, directories, files in os.walk(workspace):
        root_path = Path(root)
        add_execute_bits(root_path)
        for directory in directories:
            add_execute_bits(root_path / directory)
        for file_name in files:
            target = root_path / file_name
            mode = target.stat().st_mode
            if mode & 0o111:
                target.chmod(mode | 0o111)


def add_execute_bits(path: Path) -> None:
    path.chmod(path.stat().st_mode | 0o111)


def require_success(result: CommandResult, action: str) -> None:
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        if detail:
            raise LifecycleError(f"Error: failed to {action}: {detail}")
        raise LifecycleError(f"Error: failed to {action}")


def ignore_failure(result: CommandResult) -> None:
    _ = result
