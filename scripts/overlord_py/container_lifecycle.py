"""Container image, create, reuse, fresh, and purge lifecycle seam."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import os
from pathlib import Path
from typing import Final

from overlord_py.container_run_args import build_container_run_args
from overlord_py.docker_bind_sources import resolve_bind_source_paths
from overlord_py.engine import CommandResult, ContainerEngine
from overlord_py.paths import WorkspacePaths
from overlord_py.persisted_state_mounts import PersistedStateMounts, verify_persisted_state_mounts
from overlord_py.progress import StageReporter, noop_stage, report_stage, stage_return_message
from overlord_py.state import clear_persisted_opencode_server_state, ensure_state_dir

RESPONSIBILITY: Final = "preserve image/container lifecycle, mounts, setup timing, and removal semantics"
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


def noop_after_verification() -> None:
    return None
SETUP_OWNERSHIP_REPAIR_SCRIPT: Final = (
    "chown -R overlord:overlord /home/overlord/.cache /home/overlord/.config /home/overlord/.local /home/overlord/.npm 2>/dev/null || true\n"
    "chmod -R a+rwX /home/overlord/.cache /home/overlord/.config /home/overlord/.local /home/overlord/.npm 2>/dev/null || true\n"
)


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


def local_image_ref(paths: WorkspacePaths) -> str:
    return f"localhost/{paths.identity.image_name}:latest"


def ensure_image(engine: ContainerEngine, paths: WorkspacePaths, *, env: Mapping[str, str], stage: StageReporter = noop_stage) -> tuple[str, ...]:
    stage("Checking local Overlord image...")
    image = engine.run(["image", "inspect", local_image_ref(paths)], cwd=paths.workspace, env=env)
    if image.returncode == 0:
        return ()
    message = f"Building overlord image from {paths.repo_root}..."
    stage(message)
    build_args = ["build", *(("--load",) if engine.name == "docker" else ()), "-t", local_image_ref(paths), str(paths.repo_root)]
    build = engine.run(build_args, cwd=paths.workspace, env=env)
    require_success(build, "build image")
    return stage_return_message(stage, message)


def container_state(engine: ContainerEngine, paths: WorkspacePaths, *, env: Mapping[str, str]) -> str:
    result = engine.run(
        ["inspect", "--format", "{{.State.Status}}", paths.identity.container_name],
        cwd=paths.workspace,
        env=env,
    )
    if result.returncode != 0:
        return "missing"
    return result.stdout.strip() or "missing"


def ensure_running(
    engine: ContainerEngine,
    paths: WorkspacePaths,
    exec_env_flags: Sequence[str],
    *,
    env: Mapping[str, str],
    home: Path | None = None,
    stage: StageReporter = noop_stage,
) -> EnsureRunningResult:
    stage(f"Checking container state for {paths.identity.container_name}...")
    state = container_state(engine, paths, env=env)
    messages: list[str] = []
    setup_allowed = True
    match state:
        case "missing":
            message = f"Creating container {paths.identity.container_name}..."
            messages.extend(report_stage(stage, message))
            ensure_state_dir(paths.state)
            host_home = Path.home() if home is None else home
            bind_sources = resolve_bind_source_paths(engine, paths, env=env, home=host_home)
            run = engine.run(
                ["run", *build_container_run_args(paths, exec_env_flags, home=host_home, bind_sources=bind_sources), local_image_ref(paths), "sleep", "infinity"],
                cwd=paths.workspace,
                env=env,
            )
            require_success(run, "create container")
        case "exited":
            message = f"Starting container {paths.identity.container_name}..."
            messages.extend(report_stage(stage, message))
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
    stage(f"Repairing workspace traversal permissions for {paths.workspace}...")
    chmod_workspace_for_rootless_podman(paths.workspace)
    stage("Checking repo setup script...")
    setup_messages, setup_ran = run_workspace_setup_script(engine, paths, env=env, stage=stage)
    messages.extend(setup_messages)
    return EnsureRunningResult(state_before=state, setup_ran=setup_ran, messages=tuple(messages))


def fresh(
    engine: ContainerEngine,
    paths: WorkspacePaths,
    *,
    env: Mapping[str, str],
    stage: StageReporter = noop_stage,
    after_verification: Callable[[], None] = noop_after_verification,
) -> tuple[str, ...]:
    home = Path(env.get("HOME", str(Path.home())))
    expected_sources = resolve_bind_source_paths(engine, paths, env=env, home=home)
    verified_mounts = verify_persisted_state_mounts(
        engine,
        paths.identity.container_name,
        expected_sources=expected_sources,
        cwd=paths.workspace,
        env=env,
    )
    after_verification()
    return _fresh_verified(engine, paths, verified_mounts, env=env, stage=stage)


def _fresh_verified(
    engine: ContainerEngine,
    paths: WorkspacePaths,
    _verified_mounts: PersistedStateMounts,
    *,
    env: Mapping[str, str],
    stage: StageReporter,
) -> tuple[str, ...]:
    messages: list[str] = []
    clear_persisted_opencode_server_state(paths.state)
    stop_message = f"Stopping container {paths.identity.container_name}..."
    messages.extend(report_stage(stage, stop_message))
    ignore_failure(engine.run(["stop", paths.identity.container_name], cwd=paths.workspace, env=env))
    remove_message = f"Removing container {paths.identity.container_name}..."
    messages.extend(report_stage(stage, remove_message))
    ignore_failure(engine.run(["rm", paths.identity.container_name], cwd=paths.workspace, env=env))
    messages.append("Done. Run 'overlord' to start fresh.")
    return tuple(messages)


def purge(
    engine: ContainerEngine,
    paths: WorkspacePaths,
    *,
    env: Mapping[str, str],
    stage: StageReporter = noop_stage,
    after_verification: Callable[[], None] = noop_after_verification,
) -> tuple[str, ...]:
    container_name = paths.identity.container_name
    if engine.name == "docker":
        state = container_state(engine, paths, env=env)
        if state == "missing":
            existence = engine.run(["container", "ls", "--all", "--filter", f"name={container_name}", "--format", "{{.Names}}"], cwd=paths.workspace, env=env)
            require_success(existence, "check container existence")
            container_exists = container_name in existence.stdout.splitlines()
        else:
            container_exists = True
    else:
        existence = engine.run(["container", "exists", container_name], cwd=paths.workspace, env=env)
        if existence.returncode not in {0, 1}:
            require_success(existence, "check container existence")
        container_exists = existence.returncode == 0
    if container_exists:
        home = Path(env.get("HOME", str(Path.home())))
        expected_sources = resolve_bind_source_paths(engine, paths, env=env, home=home)
        _ = verify_persisted_state_mounts(engine, container_name, expected_sources=expected_sources, cwd=paths.workspace, env=env)
    after_verification()
    messages: list[str] = []
    if container_exists:
        remove_container_message = f"Removing container {container_name}..."
        messages.extend(report_stage(stage, remove_container_message, f"==> {remove_container_message}"))
        require_success(engine.run(["rm", "-f", container_name], cwd=paths.workspace, env=env), "remove container")
    else:
        absent_container_message = f"Container {container_name} is already absent."
        messages.extend(report_stage(stage, absent_container_message, f"==> {absent_container_message}"))
    image_ref = local_image_ref(paths)
    stage(f"Checking image {paths.identity.image_name}...")
    image = engine.run(["image", "inspect", image_ref], cwd=paths.workspace, env=env)
    if image.returncode == 0:
        remove_image_message = f"Removing image {paths.identity.image_name}..."
        messages.extend(report_stage(stage, remove_image_message, f"==> {remove_image_message}"))
        remove_image = engine.run(["rmi", "-f", image_ref], cwd=paths.workspace, env=env)
        require_success(remove_image, "remove image")
    else:
        messages.append(f"==> Image {paths.identity.image_name} is already absent.")
    messages.extend(report_stage(stage, "Pruning dangling images...", "==> Pruning dangling images..."))
    ignore_failure(engine.run(["image", "prune", "-f", "--filter", "dangling=true"], cwd=paths.workspace, env=env))
    messages.append("==> Done. Run 'overlord' to rebuild and launch.")
    return tuple(messages)


def run_workspace_setup_script(engine: ContainerEngine, paths: WorkspacePaths, *, env: Mapping[str, str], stage: StageReporter = noop_stage) -> tuple[tuple[str, ...], bool]:
    if not (paths.workspace / "setup-devcontainer.sh").is_file():
        return (("==> No setup-devcontainer.sh found; skipping repo setup.",), False)
    stage(f"Running repo-controlled devcontainer setup as root: {SETUP_SCRIPT_CONTAINER_PATH}")
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
    repair_workspace_setup_ownership(engine, paths, env=env)
    setup_messages = () if stage is not noop_stage else (f"==> Running repo-controlled devcontainer setup as root: {SETUP_SCRIPT_CONTAINER_PATH}",)
    if setup.returncode != 0:
        return ((*setup_messages, setup_failure_warning(setup)), True)
    return (setup_messages, True)


def setup_failure_warning(result: CommandResult) -> str:
    detail = result.stderr.strip() or result.stdout.strip() or "setup script exited without output"
    return (
        f"Warning: repo-controlled setup failed: {SETUP_SCRIPT_CONTAINER_PATH}\n"
        f"Exit status: {result.returncode}\n"
        f"{detail}\n"
        "Continuing OpenCode startup so you can fix setup-devcontainer.sh from inside the workspace."
    )


def repair_workspace_setup_ownership(engine: ContainerEngine, paths: WorkspacePaths, *, env: Mapping[str, str]) -> None:
    ignore_failure(engine.run(["exec", paths.identity.container_name, "sh", "-c", SETUP_OWNERSHIP_REPAIR_SCRIPT], cwd=paths.workspace, env=env))


def chmod_workspace_for_rootless_podman(workspace: Path) -> None:
    for root, directories, _ in os.walk(workspace):
        root_path = Path(root)
        add_execute_bits(root_path)
        for directory in directories:
            add_execute_bits(root_path / directory)


def add_execute_bits(path: Path) -> None:
    if path.is_symlink():
        return
    path.chmod(path.stat().st_mode | 0o111)


def require_success(result: CommandResult, action: str) -> None:
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        if detail:
            raise LifecycleError(f"Error: failed to {action}: {detail}")
        raise LifecycleError(f"Error: failed to {action}")


def ignore_failure(result: CommandResult) -> None:
    _ = result
