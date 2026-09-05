from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final
from collections.abc import Mapping

from overlord_py.engine import ContainerEngine
from overlord_py.paths import WorkspacePaths

@dataclass(frozen=True, slots=True)
class BindSourcePaths:
    workspace: Path
    zsh_data: Path
    prime_agent_data: Path
    omp_agent_data: Path
    gitconfig: Path
    ssh_dir: Path

def resolve_bind_source_paths(
    engine: ContainerEngine,
    paths: WorkspacePaths,
    *,
    env: Mapping[str, str],
    home: Path,
) -> BindSourcePaths:
    # When running inside podman/docker with host mount namespace translation,
    # we would need to translate, but for simplicity assume direct paths.
    # Keep compatibility with existing tests harness that expects translation hook.
    return BindSourcePaths(
        workspace=paths.workspace,
        zsh_data=paths.state.zsh_data,
        prime_agent_data=paths.state.prime_agent_data,
        omp_agent_data=paths.state.omp_agent_data,
        gitconfig=home / ".gitconfig",
        ssh_dir=home / ".ssh",
    )
