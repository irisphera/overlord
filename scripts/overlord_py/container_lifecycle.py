"""Fail-closed container lifecycle and recoverable workspace initialization."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
import json
from pathlib import Path
import shutil
import stat
import tempfile
import time
import uuid
from typing import Final

from overlord_py.container_run_args import build_container_run_args, engine_socket_path
from overlord_py.docker_bind_sources import bind_source_paths
from overlord_py.engine import CommandResult, ContainerEngine
from overlord_py.paths import WorkspacePaths
from overlord_py.persisted_state_mounts import PersistedStateMounts, verify_persisted_state_mounts
from overlord_py.prime_model_sync import sync_host_prime_models
from overlord_py.progress import StageReporter, noop_stage
from overlord_py.runtime_config import inject_initial_runtime_config
from overlord_py.state import ensure_state_dir, validate_state_dirs

ENTRYPOINT_LABEL: Final = "io.overlord.entrypoint-ready"
ENTRYPOINT_READY: Final = "/run/overlord-entrypoint-ready"
INITIALIZATION_COMPLETE: Final = "/var/lib/overlord/initialization-complete"
OMP_AGENT_DATA_SOURCE: Final = "/home/overlord/.omp/agent/."
OMP_AGENT_MIGRATION_PREFIX: Final = ".omp-agent-data-migration-"
OMP_AGENT_BACKUP_PREFIX: Final = ".omp-agent-data-backup-"


@dataclass(slots=True)
class LifecycleError(Exception):
    message: str
    status: int = 1

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True, slots=True)
class VerifiedContainer:
    container_id: str
    name: str
    state: str
    mounts: PersistedStateMounts
    entrypoint_ready_contract: bool
    image_id: str


@dataclass(frozen=True, slots=True)
class EnsureRunningResult:
    state_before: str
    setup_ran: bool
    messages: tuple[str, ...]
    container_id: str


def local_image_ref(paths: WorkspacePaths) -> str:
    return f"localhost/{paths.identity.image_name}:latest"


def require_success(result: CommandResult, action: str) -> None:
    if result.returncode != 0:
        detail = "\n".join(stream.strip() for stream in (result.stdout, result.stderr) if stream.strip())
        raise LifecycleError(f"Error: failed to {action} (exit {result.returncode}):\n{detail}", result.returncode)


def _inspect(engine, paths, name, *, env, image=False):
    args = ["image", "inspect", name] if image else ["container", "inspect", name]
    result = engine.run(args, cwd=paths.workspace, env=env)
    if result.returncode:
        listing = engine.run(
            ["image", "ls", "--no-trunc", "--format", "{{.Repository}}:{{.Tag}}"] if image else
            ["container", "ls", "--all", "--format", "{{.Names}}"],
            cwd=paths.workspace, env=env,
        )
        require_success(listing, "confirm resource absence")
        if name not in listing.stdout.splitlines():
            return None
        require_success(result, f"inspect {name}")
    try:
        objects = json.loads(result.stdout)
        if not isinstance(objects, list) or len(objects) != 1 or not isinstance(objects[0], dict):
            raise ValueError("expected exactly one object")
        record = objects[0]
        if not isinstance(record.get("Id"), str) or not record["Id"]:
            raise ValueError("missing immutable resource ID")
        if not isinstance(record.get("Config"), dict):
            raise ValueError("missing resource configuration")
        labels = record["Config"].get("Labels")
        if labels is not None and not isinstance(labels, dict):
            raise ValueError("invalid resource labels")
        if not image and (not isinstance(record.get("State"), dict) or not isinstance(record.get("Image"), str)):
            raise ValueError("missing container state or image ID")
        return record
    except (ValueError, TypeError) as error:
        raise LifecycleError(f"Error: invalid inspect response for {name}: {error}") from error


def ensure_image(engine: ContainerEngine, paths: WorkspacePaths, *, env: Mapping[str, str], stage: StageReporter = noop_stage) -> None:
    image = _inspect(engine, paths, local_image_ref(paths), env=env, image=True)
    if image is not None and (image.get("Config", {}).get("Labels") or {}).get(ENTRYPOINT_LABEL) == "1":
        return
    message = f"Building Overlord image from {paths.repo_root}..."
    stage(message)
    result = engine.run(["build", *(("--load",) if engine.name == "docker" else ()), "-t", local_image_ref(paths), str(paths.repo_root)], cwd=paths.workspace, env=env)
    require_success(result, "build image")


def verified_container(engine, paths, *, env) -> VerifiedContainer | None:
    for name in (paths.identity.container_name, paths.identity.legacy_container_name):
        record = _inspect(engine, paths, name, env=env)
        if record is None:
            continue
        container_id = record["Id"]
        mounts = verify_persisted_state_mounts(
            engine, container_id, expected_sources=bind_source_paths(paths),
            cwd=paths.workspace, env=env, allow_missing_omp=True, allow_legacy_access=True,
        )
        state = record.get("State", {}).get("Status")
        if not isinstance(state, str) or not state:
            raise LifecycleError("Error: container inspect omitted lifecycle state")
        return VerifiedContainer(container_id, name, state, mounts,
            (record.get("Config", {}).get("Labels") or {}).get(ENTRYPOINT_LABEL) == "1", record["Image"])
    return None


def _target_paths(paths, container_id):
    return replace(paths, identity=replace(paths.identity, container_name=container_id))


def _remove_verified(engine, paths, container, *, env, stage):
    validate_state_dirs(paths.state)
    target = _target_paths(paths, container.container_id)
    migration_needed = container.mounts.omp_agent_data is None
    if migration_needed:
        _validate_omp_migration_target(paths)
    stage(f"Stopping container {container.name}...")
    require_success(engine.run(["stop", container.container_id], cwd=paths.workspace, env=env), "stop container")
    if migration_needed:
        stage("Rescuing OMP agent state from the stopped container...")
        _rescue_omp_agent_data(engine, target, env=env)
    stage(f"Removing container {container.name}...")
    require_success(engine.run(["rm", container.container_id], cwd=paths.workspace, env=env), "remove container")


def fresh(engine: ContainerEngine, paths: WorkspacePaths, *, env: Mapping[str, str], stage: StageReporter = noop_stage) -> tuple[str, ...]:
    container = verified_container(engine, paths, env=env)
    if container is not None:
        _remove_verified(engine, paths, container, env=env, stage=stage)
    return ("Container removed; persisted workspace state retained.",) if container else ()


def purge(engine: ContainerEngine, paths: WorkspacePaths, *, env: Mapping[str, str], stage: StageReporter = noop_stage) -> tuple[str, ...]:
    container = verified_container(engine, paths, env=env)
    if container is not None:
        _remove_verified(engine, paths, container, env=env, stage=stage)
    # Identical builds can share an image ID across workspaces. Untag only this
    # workspace; deleting that ID would require force and remove other aliases.
    reference = local_image_ref(paths)
    if _inspect(engine, paths, reference, env=env, image=True) is not None:
        require_success(engine.run(["rmi", reference], cwd=paths.workspace, env=env), "remove workspace image tag")
    if container is not None:
        legacy_reference = f"localhost/{paths.identity.legacy_container_name}:latest"
        legacy_image = _inspect(engine, paths, legacy_reference, env=env, image=True)
        if legacy_image is not None and legacy_image["Id"] == container.image_id:
            require_success(engine.run(["rmi", legacy_reference], cwd=paths.workspace, env=env), "remove verified legacy image tag")
    return ("Done. Run 'overlord' to rebuild.",)


def _marker_exists(engine, paths, marker, *, env):
    result = engine.run(["exec", "-u", "0", paths.identity.container_name, "sh", "-c",
        'if [ -f "$1" ] && [ ! -L "$1" ]; then printf ready; else printf missing; fi', "sh", marker], cwd=paths.workspace, env=env)
    require_success(result, f"check {marker}")
    value = result.stdout.strip()
    if value not in {"ready", "missing"}:
        raise LifecycleError(f"Error: invalid readiness response for {marker}")
    return value == "ready"


def wait_for_entrypoint(engine, paths, *, env):
    deadline = time.monotonic() + 60
    while not _marker_exists(engine, paths, ENTRYPOINT_READY, env=env):
        if time.monotonic() >= deadline:
            raise LifecycleError("Error: entrypoint did not finish initialization within 60 seconds; inspect container logs before retrying.")
        time.sleep(0.2)


def ensure_running(engine: ContainerEngine, paths: WorkspacePaths, exec_env_flags: Sequence[str], *, env: Mapping[str, str], home: Path | None = None, stage: StageReporter = noop_stage) -> EnsureRunningResult:
    home = Path(env.get("HOME", str(Path.home()))) if home is None else home
    container = verified_container(engine, paths, env=env)
    validate_state_dirs(paths.state)
    engine_socket_path(engine_name=engine.name, env=env)
    state_before = "missing" if container is None else container.state
    if container is not None and (container.mounts.omp_agent_data is None or not container.entrypoint_ready_contract or not container.mounts.access_matches):
        _remove_verified(engine, paths, container, env=env, stage=stage)
        container = None
    ensure_state_dir(paths.state)
    sync_host_prime_models(home=home, prime_agent_data=paths.state.prime_agent_data)
    messages = []
    if container is None:
        run_args = build_container_run_args(paths, exec_env_flags, engine_name=engine.name, env=env)
        ensure_image(engine, paths, env=env, stage=stage)
        result = engine.run(["run", *run_args, local_image_ref(paths), "sleep", "infinity"], cwd=paths.workspace, env=env)
        require_success(result, "create container")
        container = verified_container(engine, paths, env=env)
        if container is None:
            raise LifecycleError("Error: newly created container disappeared")
        if container.mounts.omp_agent_data is None or not container.mounts.access_matches or not container.entrypoint_ready_contract:
            raise LifecycleError("Error: newly created container does not satisfy workspace isolation and initialization requirements; use overlord fresh.")
    elif container.name != paths.identity.container_name:
        require_success(engine.run(["rename", container.container_id, paths.identity.container_name], cwd=paths.workspace, env=env), "adopt verified legacy container")
    target = _target_paths(paths, container.container_id)
    if container.state in {"exited", "created"}:
        require_success(engine.run(["start", container.container_id], cwd=paths.workspace, env=env), "start container")
    elif container.state != "running":
        raise LifecycleError(f"Error: container has unexpected state {container.state!r}; use overlord fresh.")
    wait_for_entrypoint(engine, target, env=env)
    setup_ran = False
    if not _marker_exists(engine, target, INITIALIZATION_COMPLETE, env=env):
        setup_messages, setup_ran = run_workspace_setup_script(engine, target, env=env, stage=stage)
        messages.extend(setup_messages)
        inject_initial_runtime_config(engine, target, env=env)
        result = engine.run(["exec", "-u", "0", container.container_id, "sh", "-c",
            'install -d -m 755 /var/lib/overlord && touch /var/lib/overlord/initialization-complete'], cwd=paths.workspace, env=env)
        require_success(result, "mark initialization complete")
    return EnsureRunningResult(state_before, setup_ran, tuple(messages), container.container_id)


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
    candidates = ("/workspace/setup-devcontainer.sh", "/workspace/setup.sh")
    check = engine.run(["exec", "-u", "0", paths.identity.container_name, "sh", "-c",
        'for script in /workspace/setup-devcontainer.sh /workspace/setup.sh; do if [ -f "$script" ]; then printf "%s" "$script"; exit 0; fi; done; printf missing'], cwd=paths.workspace, env=env)
    require_success(check, "discover workspace setup script")
    script = check.stdout.strip()
    if script == "missing":
        return ([], False)
    if script not in candidates:
        raise LifecycleError("Error: invalid workspace setup discovery response")
    stage(f"Running {script}...")
    result = engine.run(["exec", "-u", "0", "-e", "HOME=/root", "-e", "USER=root", "-e", "LOGNAME=root", "-e", "SUDO_USER=overlord",
        paths.identity.container_name, "bash", script], cwd=paths.workspace, env=env)
    require_success(result, f"run {script}")
    return ([f"Workspace setup {script} completed."], True)
