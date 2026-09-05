"""Container image, create, reuse, fresh, and purge lifecycle seam."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
import shutil
import stat
import tempfile
import uuid
from typing import Final



from overlord_py.container_run_args import build_container_run_args
from overlord_py.docker_bind_sources import resolve_bind_source_paths
from overlord_py.engine import CommandResult, ContainerEngine
from overlord_py.paths import WorkspacePaths
from overlord_py.persisted_state_mounts import PersistedStateMounts, verify_persisted_state_mounts
from overlord_py.progress import StageReporter, noop_stage, report_stage, stage_return_message
from overlord_py.state import clear_persisted_server_state

RESPONSIBILITY: Final = "preserve image/container lifecycle, mounts, setup timing, and removal semantics"
SETUP_SCRIPT_CANDIDATES: Final = ("/workspace/setup-devcontainer.sh", "/workspace/setup.sh")
ROOT_SETUP_ENV: Final = (
    "HOME=/root",
    "USER=root",
    "LOGNAME=root",
    "DEBIAN_FRONTEND=noninteractive",
    "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
)

OMP_AGENT_DATA_SOURCE: Final = "/home/overlord/.omp/agent/."
OMP_AGENT_MIGRATION_PREFIX: Final = ".omp-agent-data-migration-"
OMP_AGENT_BACKUP_PREFIX: Final = ".omp-agent-data-backup-"

def noop_after_verification() -> None:
    return None

SETUP_OWNERSHIP_REPAIR_SCRIPT: Final = (
    "chown -R overlord:overlord /home/overlord/.cache /home/overlord/.config /home/overlord/.local 2>/dev/null || true\n"
    "chmod -R a+rwX /home/overlord/.cache /home/overlord/.config /home/overlord/.local 2>/dev/null || true\n"
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
    result = engine.run(["inspect", "--format", "{{.State.Status}}", paths.identity.container_name], cwd=paths.workspace, env=env)
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
            raise LifecycleError(f"Error: Container {paths.identity.container_name} is in unexpected state: {state}\nTry: overlord fresh")
    if not setup_allowed:
        # Workspace setup and ad-hoc root commands can leave XDG state files
        # (notably zsh-autocomplete logs) unwritable by the interactive user.
        # The entrypoint repair only runs when the container starts, so repair
        # again before attaching to a container that was already running.
        engine.run(
            ["exec", paths.identity.container_name, "sh", "-c", SETUP_OWNERSHIP_REPAIR_SCRIPT],
            cwd=paths.workspace,
            env=env,
        )
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
        allow_missing_omp=True,
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
    require_remove_success: bool = False,
) -> tuple[str, ...]:
    messages: list[str] = []
    migration_needed = _verified_mounts.omp_agent_data is None
    if migration_needed:
        _validate_omp_migration_target(paths)
    clear_persisted_server_state(paths.state)
    stop_message = f"Stopping container {paths.identity.container_name}..."
    messages.extend(report_stage(stage, stop_message))
    stop = engine.run(["stop", paths.identity.container_name], cwd=paths.workspace, env=env)
    if migration_needed:
        require_success(stop, "stop container before rescuing OMP agent state")
        messages.extend(report_stage(stage, "Rescuing OMP agent state from the stopped container..."))
        _rescue_omp_agent_data(engine, paths, env=env)
    else:
        ignore_failure(stop)
    remove_message = f"Removing container {paths.identity.container_name}..."
    messages.extend(report_stage(stage, remove_message))
    remove = engine.run(["rm", paths.identity.container_name], cwd=paths.workspace, env=env)
    if require_remove_success:
        require_success(remove, "remove container")
    else:
        ignore_failure(remove)
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
        verified_mounts = verify_persisted_state_mounts(
            engine,
            container_name,
            expected_sources=expected_sources,
            cwd=paths.workspace,
            env=env,
            allow_missing_omp=True,
        )
        after_verification()
        messages = list(_fresh_verified(engine, paths, verified_mounts, env=env, stage=stage, require_remove_success=True))
    else:
        after_verification()
        messages: list[str] = []
    rmi_message = f"Removing image {local_image_ref(paths)}..."
    messages.extend(report_stage(stage, rmi_message))
    rmi = engine.run(["rmi", local_image_ref(paths)], cwd=paths.workspace, env=env)
    if rmi.returncode != 0:
        # ignore if image already missing
        inspect = engine.run(["image", "inspect", local_image_ref(paths)], cwd=paths.workspace, env=env)
        if inspect.returncode == 0:
            require_success(rmi, "remove image")
    messages.append("Done. Run 'overlord' to rebuild.")
    return tuple(messages)

def _lstat_or_none(path: Path):
    try:
        return path.lstat()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise LifecycleError(f"Error: could not inspect OMP agent state path {path}: {error}") from error


def _require_real_directory(path: Path, label: str) -> None:
    snapshot = _lstat_or_none(path)
    if snapshot is None:
        raise LifecycleError(f"Error: {label} {path} is missing")
    if stat.S_ISLNK(snapshot.st_mode):
        raise LifecycleError(f"Error: unsafe {label} {path}: symbolic links are not allowed")
    if not stat.S_ISDIR(snapshot.st_mode):
        raise LifecycleError(f"Error: unsafe {label} {path}: expected a real directory")


def _validate_omp_migration_target(paths: WorkspacePaths) -> None:
    root = paths.state.root
    destination = paths.state.omp_agent_data
    if destination.parent != root:
        raise LifecycleError(f"Error: unsafe OMP agent state target {destination}: it must be directly under {root}")
    root_snapshot = _lstat_or_none(root)
    if root_snapshot is None:
        try:
            root.mkdir(parents=True)
        except OSError as error:
            raise LifecycleError(f"Error: could not create OMP agent state root {root}: {error}") from error
        root_snapshot = _lstat_or_none(root)
    if root_snapshot is None:
        raise LifecycleError(f"Error: OMP agent state root {root} disappeared during validation")
    if stat.S_ISLNK(root_snapshot.st_mode):
        raise LifecycleError(f"Error: unsafe OMP agent state root {root}: symbolic links are not allowed")
    if not stat.S_ISDIR(root_snapshot.st_mode):
        raise LifecycleError(f"Error: unsafe OMP agent state root {root}: expected a real directory")
    destination_snapshot = _lstat_or_none(destination)
    if destination_snapshot is None:
        return
    if stat.S_ISLNK(destination_snapshot.st_mode):
        raise LifecycleError(f"Error: unsafe OMP agent state target {destination}: symbolic links are not allowed")
    if not stat.S_ISDIR(destination_snapshot.st_mode):
        raise LifecycleError(f"Error: unsafe OMP agent state target {destination}: expected a real directory")


def _new_omp_backup_path(root: Path) -> Path:
    for _ in range(100):
        candidate = root / f"{OMP_AGENT_BACKUP_PREFIX}{uuid.uuid4().hex}"
        if _lstat_or_none(candidate) is None:
            return candidate
    raise LifecycleError(f"Error: could not allocate a backup path under {root}")


def _promote_omp_agent_data(temp_path: Path, destination: Path) -> None:
    _require_real_directory(temp_path, "temporary OMP agent state")
    destination_snapshot = _lstat_or_none(destination)
    backup_path: Path | None = None
    if destination_snapshot is not None:
        if stat.S_ISLNK(destination_snapshot.st_mode):
            raise LifecycleError(f"Error: unsafe OMP agent state target {destination}: symbolic links are not allowed")
        if not stat.S_ISDIR(destination_snapshot.st_mode):
            raise LifecycleError(f"Error: unsafe OMP agent state target {destination}: expected a real directory")
        backup_path = _new_omp_backup_path(destination.parent)
        try:
            destination.rename(backup_path)
        except OSError as error:
            raise LifecycleError(f"Error: could not preserve existing OMP agent state at {destination}: {error}") from error
    try:
        temp_path.replace(destination)
    except OSError as error:
        if backup_path is not None and _lstat_or_none(destination) is None:
            try:
                backup_path.rename(destination)
            except OSError as restore_error:
                raise LifecycleError(
                    f"Error: could not promote rescued OMP agent state to {destination}: {error}; "
                    f"original state remains at {backup_path}, but restore failed: {restore_error}"
                ) from error
        detail = f"; original state remains at {backup_path}" if backup_path is not None else ""
        raise LifecycleError(f"Error: could not promote rescued OMP agent state to {destination}: {error}{detail}") from error


def _cleanup_omp_migration_temp(temp_path: Path) -> None:
    try:
        snapshot = temp_path.lstat()
    except (FileNotFoundError, OSError):
        return
    try:
        if stat.S_ISDIR(snapshot.st_mode) and not stat.S_ISLNK(snapshot.st_mode):
            shutil.rmtree(temp_path)
        else:
            temp_path.unlink(missing_ok=True)
    except OSError:
        return


def _rescue_omp_agent_data(engine: ContainerEngine, paths: WorkspacePaths, *, env: Mapping[str, str]) -> None:
    root = paths.state.root
    destination = paths.state.omp_agent_data
    temp_path: Path | None = None
    try:
        try:
            temp_path = Path(tempfile.mkdtemp(prefix=OMP_AGENT_MIGRATION_PREFIX, dir=root))
        except OSError as error:
            raise LifecycleError(f"Error: could not create temporary OMP agent state under {root}: {error}") from error
        source = f"{paths.identity.container_name}:{OMP_AGENT_DATA_SOURCE}"
        try:
            copied = engine.run(["cp", source, str(temp_path)], cwd=paths.workspace, env=env)
        except OSError as error:
            raise LifecycleError(f"Error: failed to copy OMP agent state from {source}: {error}") from error
        require_success(copied, f"copy OMP agent state from {source}")
        _promote_omp_agent_data(temp_path, destination)
    finally:
        if temp_path is not None:
            _cleanup_omp_migration_temp(temp_path)


def run_workspace_setup_script(engine: ContainerEngine, paths: WorkspacePaths, *, env: Mapping[str, str], stage: StageReporter = noop_stage) -> tuple[list[str], bool]:
    # Detect which setup script exists in workspace
    check = engine.run(["exec", paths.identity.container_name, "sh", "-c", "for f in /workspace/setup-devcontainer.sh /workspace/setup.sh; do if [ -x \"$f\" ]; then echo \"$f\"; exit 0; fi; if [ -f \"$f\" ]; then echo \"$f\"; exit 0; fi; done; echo none"], cwd=paths.workspace, env=env)
    script_path = check.stdout.strip()
    if script_path == "none" or not script_path:
        return ([], False)
    stage(f"Running setup script {script_path} in {paths.identity.container_name}...")
    env_flags = [f"-e={v}" for v in ROOT_SETUP_ENV]
    result = engine.run(["exec", *env_flags, paths.identity.container_name, "bash", script_path], cwd=paths.workspace, env=env)
    # Run ownership repair after
    engine.run(["exec", paths.identity.container_name, "sh", "-c", SETUP_OWNERSHIP_REPAIR_SCRIPT], cwd=paths.workspace, env=env)
    if result.returncode != 0:
        raise LifecycleError(f"Error: setup script {script_path} failed\n{result.stderr or result.stdout}")
    return ([f"Setup script {script_path} completed."], True)

def chmod_workspace_for_rootless_podman(workspace: Path) -> None:
    try:
        workspace.chmod(0o755)
    except OSError:
        pass

def require_success(result: CommandResult, action: str) -> None:
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise LifecycleError(f"Error: failed to {action}: {detail}")

def ignore_failure(result: CommandResult) -> None:
    return None
