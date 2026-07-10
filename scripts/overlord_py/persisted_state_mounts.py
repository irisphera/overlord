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
OPENCODE_DATA_DESTINATION: Final = "/home/overlord/.local/share/opencode"
ZSH_DATA_DESTINATION: Final = "/home/overlord/.zsh_data"

type JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]


class JsonDecoder(Protocol):
    def __call__(self, document: str, /) -> JsonValue: ...


@dataclass(frozen=True, slots=True)
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
    opencode_data: VerifiedMount
    zsh_data: VerifiedMount


class EngineRunner(Protocol):
    def run(
        self,
        args: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        input_text: str | None = None,
    ) -> CommandResult: ...


def verify_persisted_state_mounts(
    engine: EngineRunner,
    container: str,
    *,
    expected_sources: BindSourcePaths,
    cwd: Path,
    env: Mapping[str, str],
) -> PersistedStateMounts:
    result = engine.run(["inspect", container], cwd=cwd, env=env)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "container inspect failed"
        raise MountSafetyFailure(f"Cannot verify persisted-state mounts: {detail}")

    mounts = _parse_inspect_mounts(result.stdout)
    workspace = _required_mount(mounts, WORKSPACE_DESTINATION)
    opencode_data = _required_mount(mounts, OPENCODE_DATA_DESTINATION)
    zsh_data = _required_mount(mounts, ZSH_DATA_DESTINATION)

    _require_source(workspace, _normalize_absolute_posix(str(expected_sources.workspace), "expected Source"))
    _require_source(opencode_data, _normalize_absolute_posix(str(expected_sources.opencode_data), "expected Source"))
    _require_source(zsh_data, _normalize_absolute_posix(str(expected_sources.zsh_data), "expected Source"))

    return PersistedStateMounts(
        workspace=_verified(workspace),
        opencode_data=_verified(opencode_data),
        zsh_data=_verified(zsh_data),
    )


def _parse_inspect_mounts(
    stdout: str,
    decoder: JsonDecoder = json.loads,
) -> tuple[InspectedMount, ...]:
    try:
        decoded = decoder(stdout)
    except json.JSONDecodeError as error:
        raise MountSafetyFailure(f"Container inspect returned malformed JSON: {error.msg}") from error

    match decoded:
        case [{"Mounts": [*raw_mounts]}]:
            return tuple(_parse_mount(raw_mount) for raw_mount in raw_mounts)
        case None | bool() | int() | float() | str() | list() | dict():
            raise MountSafetyFailure("Container inspect must return exactly one object with a Mounts array")


def _parse_mount(raw_mount: JsonValue) -> InspectedMount:
    match raw_mount:
        case {
            "Type": str() as mount_type,
            "Source": str() as source,
            "Destination": str() as destination,
            "RW": bool() as writable,
        }:
            return InspectedMount(
                mount_type=mount_type,
                source=_normalize_absolute_posix(source, "Source"),
                destination=_normalize_absolute_posix(destination, "Destination"),
                writable=writable,
            )
        case None | bool() | int() | float() | str() | list() | dict():
            raise MountSafetyFailure("Container inspect contains a mount with malformed fields")


def _normalize_absolute_posix(value: str, field: str) -> str:
    if not posixpath.isabs(value):
        raise MountSafetyFailure(f"Mount {field} must be an absolute POSIX path: {value!r}")
    return posixpath.normpath(value)


def _required_mount(mounts: tuple[InspectedMount, ...], destination: str) -> InspectedMount:
    matching = tuple(mount for mount in mounts if mount.destination == destination)
    if len(matching) != 1:
        raise MountSafetyFailure(f"Expected exactly one mount at {destination}; found {len(matching)}")

    mount = matching[0]
    if mount.mount_type != "bind":
        raise MountSafetyFailure(f"Mount at {destination} must be a bind mount")
    if not mount.writable:
        raise MountSafetyFailure(f"Mount at {destination} must be writable")
    return mount


def _require_source(mount: InspectedMount, expected_source: str) -> None:
    if mount.source != expected_source:
        raise MountSafetyFailure(
            f"Mount at {mount.destination} must use source {expected_source}; found {mount.source}"
        )


def _verified(mount: InspectedMount) -> VerifiedMount:
    return VerifiedMount(source=mount.source, destination=mount.destination)
