"""Fail-closed verification of persisted-state container bind mounts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
from pathlib import Path
import posixpath
from typing import Final, Protocol, override

from .docker_bind_sources import BindSourcePaths
from .engine import CommandResult

WORKSPACE_DESTINATION: Final = "/workspace"
ZSH_DATA_DESTINATION: Final = "/home/overlord/.zsh_data"
PRIME_AGENT_DATA_DESTINATION: Final = "/home/overlord/.prime/agent"
OMP_AGENT_DATA_DESTINATION: Final = "/home/overlord/.omp/agent"

@dataclass(slots=True)
class MountSafetyFailure(Exception):
    message: str
    @override
    def __str__(self) -> str:
        return self.message

@dataclass(frozen=True, slots=True)
class InspectedMount:
    mount_type: str
    source: str
    destination: str
    writable: bool

@dataclass(frozen=True, slots=True)
class VerifiedMount:
    source: str
    destination: str

@dataclass(frozen=True, slots=True)
class PersistedStateMounts:
    workspace: VerifiedMount
    zsh_data: VerifiedMount
    prime_agent_data: VerifiedMount
    omp_agent_data: VerifiedMount | None
    access_matches: bool

class EngineRunner(Protocol):
    def run(self, args: Sequence[str], *, cwd: Path, env: Mapping[str, str], input_text: str | None = None) -> CommandResult: ...

def verify_persisted_state_mounts(
    engine: EngineRunner,
    container: str,
    *,
    expected_sources: BindSourcePaths,
    cwd: Path,
    env: Mapping[str, str],
    allow_missing_omp: bool = False,
    allow_legacy_access: bool = False,
) -> PersistedStateMounts:
    result = engine.run(["container", "inspect", container], cwd=cwd, env=env)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "container inspect failed"
        raise MountSafetyFailure(f"Cannot verify persisted-state mounts: {detail}")
    mounts = _parse_inspect_mounts(result.stdout)
    _reject_shadowing_mounts(mounts)
    workspace = _required_mount(mounts, WORKSPACE_DESTINATION)
    zsh_data = _required_mount(mounts, ZSH_DATA_DESTINATION)
    prime_agent_data = _required_mount(mounts, PRIME_AGENT_DATA_DESTINATION)
    omp_agent_data = _optional_mount(mounts, OMP_AGENT_DATA_DESTINATION) if allow_missing_omp else _required_mount(mounts, OMP_AGENT_DATA_DESTINATION)
    _require_mount(workspace, _normalize_absolute_posix(str(expected_sources.workspace), "expected Source"))
    _require_mount(zsh_data, _normalize_absolute_posix(str(expected_sources.zsh_data), "expected Source"))
    _require_mount(prime_agent_data, _normalize_absolute_posix(str(expected_sources.prime_agent_data), "expected Source"))
    if omp_agent_data is not None:
        _require_mount(omp_agent_data, _normalize_absolute_posix(str(expected_sources.omp_agent_data), "expected Source"))
    access_matches = _access_matches(mounts, env.get("OVERLORD_ENGINE_SOCKET"))
    if not access_matches and not allow_legacy_access:
        raise MountSafetyFailure("Container mounts expose host paths outside the requested workspace/state/socket access; use overlord fresh.")
    return PersistedStateMounts(
        workspace=_verified(workspace),
        zsh_data=_verified(zsh_data),
        prime_agent_data=_verified(prime_agent_data),
        omp_agent_data=None if omp_agent_data is None else _verified(omp_agent_data),
        access_matches=access_matches,
    )

def _parse_inspect_mounts(stdout: str) -> tuple[InspectedMount, ...]:
    try:
        decoded = json.loads(stdout)
    except json.JSONDecodeError as error:
        raise MountSafetyFailure(f"Container inspect returned malformed JSON: {error.msg}") from error
    match decoded:
        case [{"Mounts": [*raw_mounts]}]:
            return tuple(_parse_mount(raw_mount) for raw_mount in raw_mounts)
        case _:
            raise MountSafetyFailure("Container inspect must return exactly one object with a Mounts array")

def _parse_mount(raw_mount) -> InspectedMount:
    match raw_mount:
        case {"Type": str() as mount_type, "Source": str() as source, "Destination": str() as destination, "RW": bool() as writable}:
            return InspectedMount(mount_type=mount_type, source=source, destination=destination, writable=writable)
        case _:
            raise MountSafetyFailure(f"Invalid mount entry: {raw_mount!r}")

def _required_mount(mounts: tuple[InspectedMount, ...], destination: str) -> InspectedMount:
    mount = _optional_mount(mounts, destination)
    if mount is None:
        raise MountSafetyFailure(f"Missing required mount at {destination}")
    return mount

def _optional_mount(mounts: tuple[InspectedMount, ...], destination: str) -> InspectedMount | None:
    matching = [mount for mount in mounts if mount.destination == destination]
    if len(matching) > 1:
        raise MountSafetyFailure(f"Duplicate mounts at {destination}")
    return matching[0] if matching else None

def _reject_shadowing_mounts(mounts: tuple[InspectedMount, ...]) -> None:
    destinations = (WORKSPACE_DESTINATION, ZSH_DATA_DESTINATION, PRIME_AGENT_DATA_DESTINATION, OMP_AGENT_DATA_DESTINATION)
    for mount in mounts:
        normalized = _normalize_absolute_posix(mount.destination, "mount Destination")
        if normalized != mount.destination:
            raise MountSafetyFailure(f"Noncanonical mount destination: {mount.destination}")
        for destination in destinations:
            if mount.destination.startswith(destination + "/"):
                raise MountSafetyFailure(f"Mount beneath {destination} would shadow persisted state: {mount.destination}")

def _access_matches(mounts: tuple[InspectedMount, ...], socket: str | None) -> bool:
    destinations = (WORKSPACE_DESTINATION, ZSH_DATA_DESTINATION, PRIME_AGENT_DATA_DESTINATION, OMP_AGENT_DATA_DESTINATION)
    extra = tuple(mount for mount in mounts if mount.destination not in destinations)
    if not socket:
        return not extra
    if len(extra) != 1 or not posixpath.isabs(socket):
        return False
    mount = extra[0]
    return (
        mount.mount_type == "bind"
        and mount.destination == "/var/run/docker.sock"
        and mount.writable
        and posixpath.isabs(mount.source)
        and posixpath.normpath(mount.source) == posixpath.normpath(socket)
    )


def _require_mount(mount: InspectedMount, expected: str) -> None:
    if mount.mount_type != "bind":
        raise MountSafetyFailure(f"Mount at {mount.destination} must be a writable bind mount (found type {mount.mount_type!r})")
    if not mount.writable:
        raise MountSafetyFailure(f"Mount at {mount.destination} must be writable")
    _require_source(mount, expected)

def _verified(mount: InspectedMount) -> VerifiedMount:
    return VerifiedMount(source=mount.source, destination=mount.destination)

def _normalize_absolute_posix(path: str, label: str) -> str:
    normalized = posixpath.normpath(path)
    if not posixpath.isabs(normalized):
        raise MountSafetyFailure(f"{label} must be absolute: {path}")
    return normalized

def _require_source(mount: InspectedMount, expected: str) -> None:
    normalized_source = _normalize_absolute_posix(mount.source, "mount Source")
    if normalized_source != expected:
        raise MountSafetyFailure(f"Mount at {mount.destination} has unexpected source {mount.source!r} (expected {expected!r})")
