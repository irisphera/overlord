"""Persistent launcher state seam."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique
import os
from pathlib import Path
import stat
import tempfile
from typing import Final, assert_never, override

from overlord_py.paths import ManagedStatePaths, StatePaths

RESPONSIBILITY: Final = "create .overlord state"
MANAGED_GITIGNORE_ENTRIES: Final = (".overlord/", ".omo", ".codegraph")
GITIGNORE_TEMP_PREFIX: Final = ".gitignore.overlord-"

@unique
class NodeKind(StrEnum):
    MISSING = "missing"
    DIRECTORY = "directory"
    SYMLINK = "symlink"
    FILE = "file"
    OTHER = "other node"

@unique
class PairAction(StrEnum):
    CREATE = "create"
    LINK = "link"
    MIGRATE = "migrate"
    READY = "ready"

@dataclass(frozen=True, slots=True)
class NodeSnapshot:
    path: Path
    kind: NodeKind
    link_target: str | None = None
    mode: int | None = None

@dataclass(frozen=True, slots=True)
class PairPlan:
    paths: ManagedStatePaths
    action: PairAction

@dataclass(frozen=True, slots=True)
class ManagedStateError(RuntimeError):
    path: Path
    reason: str
    @override
    def __str__(self) -> str:
        return (
            f"Error: unsafe managed state at {self.path}: {self.reason}. "
            "Existing data was preserved; no copy, merge, or delete was attempted."
        )

@dataclass(frozen=True, slots=True)
class StateEnsureResult:
    zsh_data_created: bool
    prime_agent_data_created: bool
    gitignore_created: bool
    gitignore_appended: bool

def describe() -> str:
    return RESPONSIBILITY

def ensure_state_dir(paths: StatePaths) -> StateEnsureResult:
    root_snapshot = classify_node(paths.root)
    if root_snapshot.kind not in {NodeKind.MISSING, NodeKind.DIRECTORY}:
        raise ManagedStateError(paths.root, f"expected a real directory or no entry, found {root_snapshot.kind}")

    gitignore = paths.root.parent / ".gitignore"
    pair_snapshots = tuple(
        (pair, classify_node(pair.workspace_entry), classify_node(pair.managed_directory))
        for pair in (paths.omo, paths.codegraph)
    )
    gitignore_snapshot = classify_node(gitignore)
    if gitignore_snapshot.kind not in {NodeKind.MISSING, NodeKind.FILE}:
        raise ManagedStateError(gitignore, f"expected a regular file or no entry, found {gitignore_snapshot.kind}")
    pair_plans = tuple(plan_pair(*snapshot) for snapshot in pair_snapshots)

    zsh_preexisting = paths.zsh_data.is_dir()
    prime_preexisting = paths.prime_agent_data.is_dir()
    gitignore_created = gitignore_snapshot.kind is NodeKind.MISSING
    gitignore_appended = append_state_gitignore(gitignore, gitignore_snapshot)
    create_directory(paths.zsh_data, parents=True, exist_ok=True)
    create_directory(paths.prime_agent_data, parents=True, exist_ok=True)
    for plan in pair_plans:
        apply_pair_plan(plan)
    return StateEnsureResult(
        zsh_data_created=not zsh_preexisting,
        prime_agent_data_created=not prime_preexisting,
        gitignore_created=gitignore_created,
        gitignore_appended=gitignore_appended,
    )

def classify_node(path: Path) -> NodeSnapshot:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return NodeSnapshot(path, NodeKind.MISSING)
    except OSError as error:
        raise ManagedStateError(path, f"could not inspect path: {error}") from error
    if stat.S_ISDIR(metadata.st_mode):
        return NodeSnapshot(path, NodeKind.DIRECTORY)
    if stat.S_ISLNK(metadata.st_mode):
        try:
            link_target = os.readlink(path)
        except OSError as error:
            raise ManagedStateError(path, f"could not read symbolic link: {error}") from error
        return NodeSnapshot(path, NodeKind.SYMLINK, link_target)
    if stat.S_ISREG(metadata.st_mode):
        return NodeSnapshot(path, NodeKind.FILE, mode=stat.S_IMODE(metadata.st_mode))
    return NodeSnapshot(path, NodeKind.OTHER)

def plan_pair(paths: ManagedStatePaths, root: NodeSnapshot, target: NodeSnapshot) -> PairPlan:
    if target.kind not in {NodeKind.MISSING, NodeKind.DIRECTORY}:
        raise ManagedStateError(target.path, f"managed target must be a real directory, found {target.kind}")
    match root.kind:
        case NodeKind.MISSING:
            action = PairAction.CREATE if target.kind is NodeKind.MISSING else PairAction.LINK
        case NodeKind.DIRECTORY:
            if target.kind is NodeKind.DIRECTORY:
                raise ManagedStateError(root.path, f"both {root.path} and {target.path} contain independent directories")
            action = PairAction.MIGRATE
        case NodeKind.SYMLINK:
            expected_target = os.fspath(paths.relative_target)
            if root.link_target != expected_target:
                raise ManagedStateError(root.path, f"expected literal relative link {expected_target!r}, found {root.link_target!r}")
            if target.kind is NodeKind.MISSING:
                raise ManagedStateError(root.path, f"managed link is broken because {target.path} is not a real directory")
            action = PairAction.READY
        case NodeKind.FILE | NodeKind.OTHER:
            raise ManagedStateError(root.path, f"workspace entry must be a managed symbolic link or lone directory, found {root.kind}")
        case unreachable:
            assert_never(unreachable)
    return PairPlan(paths, action)

def apply_pair_plan(plan: PairPlan) -> None:
    match plan.action:
        case PairAction.CREATE:
            create_directory(plan.paths.managed_directory)
            install_managed_link(plan.paths)
        case PairAction.LINK:
            install_managed_link(plan.paths)
        case PairAction.MIGRATE:
            migrate_directory(plan.paths)
        case PairAction.READY:
            pass
        case unreachable:
            assert_never(unreachable)

def create_directory(path: Path, *, parents: bool = False, exist_ok: bool = False) -> None:
    try:
        path.mkdir(parents=parents, exist_ok=exist_ok)
    except OSError as error:
        raise ManagedStateError(path, f"could not create directory: {error}") from error

def install_managed_link(paths: ManagedStatePaths) -> None:
    try:
        paths.workspace_entry.symlink_to(paths.relative_target)
    except OSError as error:
        raise ManagedStateError(paths.workspace_entry, f"could not create managed link: {error}") from error

def migrate_directory(paths: ManagedStatePaths) -> None:
    try:
        paths.workspace_entry.rename(paths.managed_directory)
    except OSError as error:
        raise ManagedStateError(paths.workspace_entry, f"could not migrate directory: {error}") from error
    install_managed_link(paths)

def append_state_gitignore(gitignore: Path, snapshot: NodeSnapshot) -> bool:
    if snapshot.kind is NodeKind.MISSING:
        try:
            gitignore.write_text("\n".join(MANAGED_GITIGNORE_ENTRIES) + "\n", encoding="utf-8")
        except OSError as error:
            raise ManagedStateError(gitignore, f"could not create .gitignore: {error}") from error
        return True
    # existing file - append missing entries
    try:
        content = gitignore.read_text(encoding="utf-8")
    except OSError as error:
        raise ManagedStateError(gitignore, f"could not read .gitignore: {error}") from error
    lines = set(line.strip() for line in content.splitlines())
    missing = [e for e in MANAGED_GITIGNORE_ENTRIES if e not in lines]
    if not missing:
        return False
    try:
        with gitignore.open("a", encoding="utf-8") as f:
            if content and not content.endswith("\n"):
                f.write("\n")
            for entry in missing:
                f.write(entry + "\n")
    except OSError as error:
        raise ManagedStateError(gitignore, f"could not update .gitignore: {error}") from error
    return True

def clear_persisted_server_state(state: StatePaths) -> None:
    # Legacy cleanup - remove old server pid/log if present
    for name in ("overlord-serve.pid", "overlord-serve.log", "web-proxy.pid", "web-proxy.port", "web-proxy.log"):
        try:
            (state.root / name).unlink(missing_ok=True)
        except OSError:
            pass

def chmod_workspace_for_rootless_podman(workspace: Path) -> None:
    try:
        workspace.chmod(0o755)
    except OSError:
        pass
