from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
import socket
from typing import Final

from overlord_py.engine import ContainerEngine
from overlord_py.paths import WorkspacePaths

RESPONSIBILITY: Final = "map paths visible inside the launcher container to Docker-daemon host paths"
MOUNT_FORMAT: Final = '{{range .Mounts}}{{printf "%s\t%s\n" .Source .Destination}}{{end}}'


@dataclass(frozen=True, slots=True)
class BindSourcePaths:
    workspace: Path
    opencode_data: Path
    zsh_data: Path
    gitconfig: Path
    ssh_dir: Path


@dataclass(frozen=True, slots=True)
class ContainerMount:
    source: Path
    destination: Path


def resolve_bind_source_paths(
    engine: ContainerEngine,
    paths: WorkspacePaths,
    *,
    env: Mapping[str, str],
    home: Path,
) -> BindSourcePaths:
    mounts = current_container_mounts(engine, paths, env=env)
    return BindSourcePaths(
        workspace=translate_path(paths.workspace, mounts),
        opencode_data=translate_path(paths.state.opencode_data, mounts),
        zsh_data=translate_path(paths.state.zsh_data, mounts),
        gitconfig=translate_path(home / ".gitconfig", mounts),
        ssh_dir=translate_path(home / ".ssh", mounts),
    )


def current_container_mounts(engine: ContainerEngine, paths: WorkspacePaths, *, env: Mapping[str, str]) -> tuple[ContainerMount, ...]:
    current_container_name = socket.gethostname()
    inspect = engine.run(
        ["inspect", "--format", MOUNT_FORMAT, current_container_name],
        cwd=paths.workspace,
        env=env,
    )
    if inspect.returncode != 0:
        return ()
    return tuple(parse_mounts(inspect.stdout.splitlines()))


def parse_mounts(lines: Iterable[str]) -> tuple[ContainerMount, ...]:
    mounts: list[ContainerMount] = []
    for line in lines:
        line = line.rstrip("\r\n")
        source_text, separator, destination_text = line.partition("\t")
        if separator == "" or source_text == "" or destination_text == "":
            continue
        mounts.append(ContainerMount(source=Path(source_text), destination=Path(destination_text)))
    return tuple(mounts)


def translate_path(path: Path, mounts: Iterable[ContainerMount]) -> Path:
    best = best_mount_for(path, mounts)
    if best is None:
        return path
    return best.source / path.relative_to(best.destination)


def best_mount_for(path: Path, mounts: Iterable[ContainerMount]) -> ContainerMount | None:
    best: ContainerMount | None = None
    for mount in mounts:
        if not is_relative_to(path, mount.destination):
            continue
        if best is None or len(mount.destination.parts) > len(best.destination.parts):
            best = mount
    return best


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True
