"""Repository, workspace, and persistent state path planning seam."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Final, override

RESPONSIBILITY: Final = "derive repo roots, workspace identity, and .overlord paths"
SLUG_INVALID_CHARS: Final = re.compile(r"[^a-z0-9._-]")
GITDIR_FILE: Final = re.compile(r"\Agitdir: (?P<path>[^\r\n]+)\n?\Z")

@dataclass(frozen=True, slots=True)
class GitdirOutsideWorkspaceError(Exception):
    workspace: Path
    gitdir: Path
    @override
    def __str__(self) -> str:
        return (
            "Error: workspace Git metadata resolves outside the workspace bind mount.\n"
            f"Resolved workspace: {self.workspace}\n"
            f"Resolved gitdir: {self.gitdir}\n"
            "Run Overlord from the containing repository or use a standalone clone."
        )

@dataclass(frozen=True, slots=True)
class WorkspaceIdentity:
    workspace_name: str
    workspace_slug: str
    image_name: str
    container_name: str
    zellij_session: str

@dataclass(frozen=True, slots=True)
class ManagedStatePaths:
    workspace_entry: Path
    managed_directory: Path
    relative_target: Path

@dataclass(frozen=True, slots=True)
class StatePaths:
    root: Path
    zsh_data: Path
    prime_agent_data: Path
    omo: ManagedStatePaths
    codegraph: ManagedStatePaths

@dataclass(frozen=True, slots=True)
class WorkspacePaths:
    script_path: Path
    script_dir: Path
    repo_root: Path
    workspace: Path
    identity: WorkspaceIdentity
    state: StatePaths

def describe() -> str:
    return RESPONSIBILITY

def resolve_script_path(script_path: Path) -> Path:
    return script_path.resolve()

def repo_root_from_script(script_path: Path) -> Path:
    return resolve_script_path(script_path).parent.parent

def workspace_identity(workspace: Path) -> WorkspaceIdentity:
    workspace_name = workspace.resolve().name
    workspace_slug = SLUG_INVALID_CHARS.sub("-", workspace_name.lower())
    return WorkspaceIdentity(
        workspace_name=workspace_name,
        workspace_slug=workspace_slug,
        image_name=f"overlord-{workspace_slug}",
        container_name=f"overlord-{workspace_slug}",
        zellij_session=workspace_name,
    )

def state_paths(workspace: Path) -> StatePaths:
    root = workspace / ".overlord"
    return StatePaths(
        root=root,
        zsh_data=root / "zsh-data",
        prime_agent_data=root / "prime-agent-data",
        omo=managed_state_paths(workspace, root, ".omo"),
        codegraph=managed_state_paths(workspace, root, ".codegraph"),
    )

def managed_state_paths(workspace: Path, state_root: Path, name: str) -> ManagedStatePaths:
    return ManagedStatePaths(
        workspace_entry=workspace / name,
        managed_directory=state_root / name,
        relative_target=Path(".overlord") / name,
    )

def ensure_gitdir_within_workspace(paths: WorkspacePaths) -> None:
    git_entry = paths.workspace / ".git"
    if not git_entry.is_file():
        return
    match = GITDIR_FILE.fullmatch(git_entry.read_text(encoding="utf-8"))
    if match is None:
        return
    gitdir = (git_entry.parent / match["path"]).resolve(strict=False)
    if not gitdir.is_relative_to(paths.workspace):
        raise GitdirOutsideWorkspaceError(workspace=paths.workspace, gitdir=gitdir)

def build_workspace_paths(workspace: Path, *, script_path: Path) -> WorkspacePaths:
    resolved_script = resolve_script_path(script_path)
    resolved_workspace = workspace.resolve()
    return WorkspacePaths(
        script_path=resolved_script,
        script_dir=resolved_script.parent,
        repo_root=resolved_script.parent.parent,
        workspace=resolved_workspace,
        identity=workspace_identity(resolved_workspace),
        state=state_paths(resolved_workspace),
    )
